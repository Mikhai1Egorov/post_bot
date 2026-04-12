from __future__ import annotations

from datetime import datetime
from io import BytesIO
import json
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.infrastructure.external.gpt_clients import OpenAIImageClient, OpenAILLMClient, OpenAIResearchClient  # noqa: E402
from post_bot.infrastructure.external.local_publisher import LocalArtifactPublisher  # noqa: E402
from post_bot.infrastructure.external.telegram_publisher import TelegramBotPublisher  # noqa: E402
from post_bot.infrastructure.telegram.http_gateway import TelegramHttpGateway  # noqa: E402
from post_bot.shared.errors import ExternalDependencyError, ValidationError  # noqa: E402


class _FakeResponse:
    def __init__(self, *, status: int = 200, payload: dict[str, object]) -> None:
        self.status = status
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False



class _RawHttpResponse:
    def __init__(self, body: bytes, *, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeBinaryResponse:
    def __init__(self, *, body: bytes, status: int = 200, content_type: str = "image/png") -> None:
        self.status = status
        self._body = body
        self.headers = {"Content-Type": content_type}

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False
class _FakeTelegramGateway:
    def __init__(self, *, return_message_ids: bool = False) -> None:
        self.calls: list[dict[str, object]] = []
        self.photo_calls: list[dict[str, object]] = []
        self._return_message_ids = return_message_ids
        self._next_message_id = 1

    def _next_result(self) -> dict[str, object] | None:
        if not self._return_message_ids:
            return None
        result = {"message_id": self._next_message_id}
        self._next_message_id += 1
        return result

    def send_message(
        self,
        *,
        chat_id: int | str,
        text: str,
        reply_markup: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        self.calls.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return self._next_result()

    def send_photo(
        self,
        *,
        chat_id: int | str,
        photo: str | bytes,
        caption: str | None = None,
        file_name: str | None = None,
    ) -> dict[str, object] | None:
        self.photo_calls.append({"chat_id": chat_id, "photo": photo, "caption": caption, "file_name": file_name})
        return self._next_result()


class _FailingTelegramGateway(_FakeTelegramGateway):
    def __init__(self, *, fail_on_message_call: int) -> None:
        super().__init__(return_message_ids=True)
        self._fail_on_message_call = fail_on_message_call

    def send_message(
        self,
        *,
        chat_id: int | str,
        text: str,
        reply_markup: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        call_number = len(self.calls) + 1
        if call_number == self._fail_on_message_call:
            raise ExternalDependencyError(
                code="TELEGRAM_HTTP_ERROR",
                message="Telegram HTTP request failed.",
                details={"status": 502, "method": "sendMessage"},
                retryable=True,
            )
        return super().send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)


class ExternalAdaptersTests(unittest.TestCase):
    def test_research_client_parses_sources_from_model_json(self) -> None:
        client = OpenAIResearchClient(api_key="sk-test", model_name="gpt-test")

        with patch(
            "post_bot.infrastructure.external.gpt_clients.urlopen",
            return_value=_FakeResponse(
                payload={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "sources": [
                                            {
                                                "source_url": "https://example.com/a",
                                                "source_title": "A",
                                                "source_language_code": "en",
                                                "published_at": "2026-04-08T10:00:00Z",
                                                "source_payload_json": {"rank": 1},
                                            }
                                        ]
                                    }
                                )
                            }
                        }
                    ]
                }
            ),
        ) as mocked_open:
            result = client.collect(title="AI", keywords="ai")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].source_url, "https://example.com/a")
        self.assertEqual(result[0].source_language_code, "en")
        self.assertEqual(result[0].published_at, datetime(2026, 4, 8, 10, 0, 0))

        request = mocked_open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-test")

    def test_research_client_rejects_invalid_json_payload(self) -> None:
        client = OpenAIResearchClient(api_key="sk-test", model_name="gpt-test")

        with patch(
            "post_bot.infrastructure.external.gpt_clients.urlopen",
            return_value=_FakeResponse(
                payload={
                    "choices": [
                        {
                            "message": {
                                "content": "not-json",
                            }
                        }
                    ]
                }
            ),
        ):
            with self.assertRaises(ExternalDependencyError) as context:
                client.collect(title="AI", keywords="ai")

        self.assertEqual(context.exception.code, "RESEARCH_RESPONSE_INVALID")
        self.assertFalse(context.exception.retryable)

    def test_llm_client_retries_on_http_5xx(self) -> None:
        client = OpenAILLMClient(api_key="sk-test")

        with patch(
            "post_bot.infrastructure.external.gpt_clients.urlopen",
            side_effect=HTTPError(
                url="https://api.openai.com/v1/chat/completions",
                code=503,
                msg="Service Unavailable",
                hdrs=None,
                fp=BytesIO(b"{}"),
            ),
        ):
            with self.assertRaises(ExternalDependencyError) as context:
                client.generate(model_name="gpt-test", prompt="prompt", response_language="en")

        self.assertEqual(context.exception.code, "OPENAI_HTTP_ERROR")
        self.assertTrue(context.exception.retryable)

    def test_llm_client_rejects_empty_content(self) -> None:
        client = OpenAILLMClient(api_key="sk-test")

        with patch(
            "post_bot.infrastructure.external.gpt_clients.urlopen",
            return_value=_FakeResponse(
                payload={
                    "choices": [
                        {
                            "message": {
                                "content": "   ",
                            }
                        }
                    ]
                }
            ),
        ):
            with self.assertRaises(ExternalDependencyError) as context:
                client.generate(model_name="gpt-test", prompt="prompt", response_language="en")

        self.assertEqual(context.exception.code, "OPENAI_RESPONSE_INVALID")
        self.assertFalse(context.exception.retryable)

    def test_telegram_http_gateway_send_message_fails_fast_on_timeout(self) -> None:
        gateway = TelegramHttpGateway(bot_token="123:abc", timeout_seconds=15)

        with patch(
            "post_bot.infrastructure.telegram.http_gateway.urlopen",
            side_effect=TimeoutError("read timed out"),
        ) as mocked_open:
            with self.assertRaises(ExternalDependencyError) as context:
                gateway.send_message(chat_id=1, text="hello")

        self.assertEqual(mocked_open.call_count, 1)
        self.assertEqual(context.exception.code, "TELEGRAM_TIMEOUT")
        self.assertEqual(context.exception.details.get("attempt"), 1)
        self.assertEqual(context.exception.details.get("max_attempts"), 1)

    def test_telegram_http_gateway_retries_get_updates_on_transient_timeout(self) -> None:
        gateway = TelegramHttpGateway(bot_token="123:abc", timeout_seconds=5)

        with patch(
            "post_bot.infrastructure.telegram.http_gateway.urlopen",
            side_effect=[
                TimeoutError("read timed out"),
                _RawHttpResponse(b'{"ok": true, "result": []}'),
            ],
        ):
            result = gateway.get_updates(offset=None, timeout_seconds=30)

        self.assertEqual(result, [])

    def test_telegram_http_gateway_uses_long_poll_timeout_for_get_updates(self) -> None:
        gateway = TelegramHttpGateway(bot_token="123:abc", timeout_seconds=5)
        captured_timeouts: list[float] = []

        def _capture(request, timeout=0.0):
            _ = request
            captured_timeouts.append(float(timeout))
            return _RawHttpResponse(b'{"ok": true, "result": []}')

        with patch("post_bot.infrastructure.telegram.http_gateway.urlopen", side_effect=_capture):
            gateway.get_updates(offset=None, timeout_seconds=30)

        self.assertTrue(captured_timeouts)
        self.assertGreaterEqual(captured_timeouts[0], 35.0)

    def test_telegram_http_gateway_returns_conflict_code_for_409_polling(self) -> None:
        gateway = TelegramHttpGateway(bot_token="123:abc", timeout_seconds=0.01)
        conflict_payload = {
            "ok": False,
            "error_code": 409,
            "description": "Conflict: terminated by other getUpdates request",
        }
        http_error = HTTPError(
            url="https://api.telegram.org/bot123:abc/getUpdates",
            code=409,
            msg="Conflict",
            hdrs={},
            fp=BytesIO(json.dumps(conflict_payload).encode("utf-8")),
        )

        with patch("post_bot.infrastructure.telegram.http_gateway.urlopen", side_effect=http_error):
            with self.assertRaises(ExternalDependencyError) as context:
                gateway.get_updates(offset=None, timeout_seconds=1)

        error = context.exception
        self.assertEqual(error.code, "TELEGRAM_POLLING_CONFLICT")
        self.assertFalse(error.retryable)
        self.assertEqual(error.details.get("status"), 409)
        self.assertIn("terminated by other getUpdates request", str(error.details.get("body")))

    def test_image_client_parses_b64_response(self) -> None:
        client = OpenAIImageClient(api_key="sk-test", model_name="gpt-image-1")

        with patch(
            "post_bot.infrastructure.external.gpt_clients.urlopen",
            return_value=_FakeResponse(payload={"data": [{"b64_json": "ZmFrZS1wbmc="}]})
        ):
            result = client.generate_cover(
                task_id=11,
                article_title="Title",
                article_topic="Topic",
                article_lead="Lead",
            )

        self.assertEqual(result.mime_type, "image/png")
        self.assertEqual(result.content, b"fake-png")
        self.assertIsNone(result.image_url)

    def test_image_client_uses_minimal_compatible_payload(self) -> None:
        client = OpenAIImageClient(api_key="sk-test", model_name="gpt-image-1")
        captured_body: dict[str, object] = {}

        def _capture(request, timeout=30.0):
            del timeout
            body = getattr(request, "data", b"")
            captured_body.update(json.loads((body or b"{}").decode("utf-8")))
            return _FakeResponse(payload={"data": [{"b64_json": "ZmFrZS1wbmc="}]})

        with patch("post_bot.infrastructure.external.gpt_clients.urlopen", side_effect=_capture):
            client.generate_cover(
                task_id=111,
                article_title="Title",
                article_topic="Topic",
                article_lead="Lead",
            )

        self.assertEqual(captured_body.get("model"), "gpt-image-1")
        self.assertEqual(captured_body.get("size"), "1024x1024")
        self.assertIn("prompt", captured_body)
        self.assertNotIn("response_format", captured_body)
        self.assertNotIn("quality", captured_body)
        self.assertNotIn("n", captured_body)

    def test_image_client_http_400_surfaces_openai_error_details(self) -> None:
        client = OpenAIImageClient(api_key="sk-test", model_name="gpt-image-1")
        error_payload = {
            "error": {
                "type": "invalid_request_error",
                "code": "invalid_size",
                "param": "size",
                "message": "Invalid size value.",
            }
        }
        http_error = HTTPError(
            url="https://api.openai.com/v1/images/generations",
            code=400,
            msg="Bad Request",
            hdrs={"x-request-id": "req_test_123"},
            fp=BytesIO(json.dumps(error_payload).encode("utf-8")),
        )

        with patch("post_bot.infrastructure.external.gpt_clients.urlopen", side_effect=http_error):
            with self.assertRaises(ExternalDependencyError) as context:
                client.generate_cover(
                    task_id=211,
                    article_title="Title",
                    article_topic="Topic",
                    article_lead="Lead",
                )

        error = context.exception
        self.assertEqual(error.code, "OPENAI_IMAGE_HTTP_ERROR")
        self.assertEqual(error.details.get("status_code"), 400)
        self.assertEqual(error.details.get("request_id"), "req_test_123")
        self.assertEqual(error.details.get("endpoint_kind"), "images.generate")
        self.assertEqual(error.details.get("image_model"), "gpt-image-1")
        self.assertEqual(error.details.get("openai_error_type"), "invalid_request_error")
        self.assertEqual(error.details.get("openai_error_code"), "invalid_size")
        self.assertEqual(error.details.get("openai_error_param"), "size")
        self.assertEqual(error.details.get("openai_error_message"), "Invalid size value.")
        self.assertFalse(bool(error.details.get("payload_has_response_format")))

    def test_image_client_retries_on_timeout_and_succeeds(self) -> None:
        client = OpenAIImageClient(api_key="sk-test", model_name="gpt-image-1", timeout_seconds=5)

        with patch(
            "post_bot.infrastructure.external.gpt_clients.urlopen",
            side_effect=[
                TimeoutError("read timed out"),
                _FakeResponse(payload={"data": [{"b64_json": "ZmFrZS1wbmc="}]}),
            ],
        ):
            result = client.generate_cover(
                task_id=212,
                article_title="Retry Title",
                article_topic="Retry Topic",
                article_lead="Retry Lead",
            )

        self.assertEqual(result.content, b"fake-png")
        self.assertEqual(result.mime_type, "image/png")

    def test_image_client_supports_url_response_and_downloads_binary(self) -> None:
        client = OpenAIImageClient(api_key="sk-test", model_name="gpt-image-1")

        with patch(
            "post_bot.infrastructure.external.gpt_clients.urlopen",
            side_effect=[
                _FakeResponse(payload={"data": [{"url": "https://images.example/cover.png"}]}),
                _FakeBinaryResponse(body=b"\\x89PNG\\r\\n\\x1a\\n....", content_type="image/png"),
            ],
        ):
            result = client.generate_cover(
                task_id=12,
                article_title="Title",
                article_topic="Topic",
                article_lead="Lead",
            )

        self.assertEqual(result.image_url, "https://images.example/cover.png")
        self.assertEqual(result.mime_type, "image/png")
        self.assertIsNotNone(result.content)
        self.assertGreater(len(result.content or b""), 8)

    def test_image_client_url_download_failure_keeps_url_for_delivery(self) -> None:
        client = OpenAIImageClient(api_key="sk-test", model_name="gpt-image-1")

        with patch(
            "post_bot.infrastructure.external.gpt_clients.urlopen",
            side_effect=[
                _FakeResponse(payload={"data": [{"url": "https://images.example/cover.webp"}]}),
                URLError("temporary dns failure"),
            ],
        ):
            result = client.generate_cover(
                task_id=13,
                article_title="Title",
                article_topic="Topic",
                article_lead="Lead",
            )

        self.assertEqual(result.image_url, "https://images.example/cover.webp")
        self.assertIsNone(result.content)
        self.assertIsNone(result.mime_type)
    def test_local_publisher_returns_manual_artifact_payload(self) -> None:
        publisher = LocalArtifactPublisher()

        external_id, payload = publisher.publish(
            channel="@news",
            html="<article>ok</article>",
            scheduled_for=datetime(2026, 4, 8, 12, 30, 1),
        )

        self.assertIsNone(external_id)
        self.assertEqual(payload["delivery"], "manual_artifact")
        self.assertEqual(payload["channel"], "@news")
        self.assertEqual(payload["scheduled_for"], "2026-04-08T12:30:01")

    def test_telegram_publisher_posts_to_resolved_channel_without_image(self) -> None:
        gateway = _FakeTelegramGateway()
        publisher = TelegramBotPublisher(gateway=gateway)

        external_id, payload = publisher.publish(
            channel="https://t.me/my_channel",
            html="<article><h1>Title</h1><p>Hello</p></article>",
            scheduled_for=datetime(2026, 4, 8, 12, 30, 1),
        )

        self.assertIsNone(external_id)
        self.assertEqual(len(gateway.calls), 1)
        self.assertEqual(len(gateway.photo_calls), 0)
        self.assertEqual(gateway.calls[0]["chat_id"], "@my_channel")
        self.assertIn("Title", str(gateway.calls[0]["text"]))
        self.assertIn("Hello", str(gateway.calls[0]["text"]))
        self.assertEqual(payload["resolved_chat_id"], "@my_channel")
        self.assertFalse(bool(payload["photo_sent"]))

    def test_telegram_publisher_returns_external_message_id_when_gateway_provides_it(self) -> None:
        gateway = _FakeTelegramGateway(return_message_ids=True)
        publisher = TelegramBotPublisher(gateway=gateway)

        external_id, payload = publisher.publish(
            channel="@my_channel",
            html="<article><h1>Title</h1><p>Hello</p></article>",
            scheduled_for=None,
        )

        self.assertEqual(external_id, "1")
        self.assertEqual(payload["external_message_id"], "1")

    def test_telegram_publisher_with_image_sends_cover_then_full_article(self) -> None:
        gateway = _FakeTelegramGateway(return_message_ids=True)
        publisher = TelegramBotPublisher(gateway=gateway)

        external_id, payload = publisher.publish(
            channel="@my_channel",
            html=(
                "<article><h1>Title</h1>"
                "<p>Lead paragraph.</p>"
                "<figure class=\"image-block\"><img src=\"https://picsum.photos/seed/ai/1600/900\" alt=\"Article image\" /></figure>"
                "<h2>Section A</h2><p>Body paragraph.</p>"
                "<footer class=\"user-footer\"><p>Read more https://example.com</p></footer></article>"
            ),
            scheduled_for=None,
        )

        self.assertEqual(external_id, "1")
        self.assertEqual(len(gateway.photo_calls), 1)
        self.assertEqual(len(gateway.calls), 1)
        self.assertEqual(gateway.photo_calls[0]["photo"], "https://picsum.photos/seed/ai/1600/900")
        self.assertIn("Title", str(gateway.photo_calls[0]["caption"] or ""))
        self.assertIn("Lead paragraph", str(gateway.photo_calls[0]["caption"] or ""))
        body_text = str(gateway.calls[0]["text"])
        self.assertIn("Title", body_text)
        self.assertIn("Lead paragraph", body_text)
        self.assertIn("Section A", body_text)
        self.assertIn("Read more https://example.com", body_text)
        self.assertTrue(bool(payload["photo_sent"]))
        self.assertEqual(payload["publisher_branch"], "send_photo_then_messages")
        self.assertEqual(payload["article_chunks_count"], 1)

    def test_telegram_publisher_keeps_cover_caption_under_safe_limit(self) -> None:
        gateway = _FakeTelegramGateway()
        publisher = TelegramBotPublisher(gateway=gateway)

        long_lead = " ".join(["lead"] * 500)
        _external_id, payload = publisher.publish(
            channel="@news",
            html=(
                "<article><h1>Very Long Title</h1>"
                f"<p>{long_lead}</p>"
                "<figure><img src=\"https://picsum.photos/seed/long/1600/900\" alt=\"Image\" /></figure>"
                "<p>Body.</p></article>"
            ),
            scheduled_for=None,
        )

        self.assertEqual(len(gateway.photo_calls), 1)
        caption = str(gateway.photo_calls[0]["caption"] or "")
        self.assertLessEqual(len(caption), 900)
        self.assertGreaterEqual(len(gateway.calls), 1)

    def test_telegram_publisher_splits_long_messages(self) -> None:
        gateway = _FakeTelegramGateway()
        publisher = TelegramBotPublisher(gateway=gateway)

        words = "word " * 2000
        _external_id, payload = publisher.publish(
            channel="@news",
            html=(
                "<article><h1>Title</h1>"
                "<h2>Section 1</h2>"
                f"<p>{words}</p>"
                "<h2>Section 2</h2>"
                f"<p>{words}</p>"
                "</article>"
            ),
            scheduled_for=None,
        )

        self.assertGreater(len(gateway.calls), 1)
        self.assertTrue(all(len(str(call["text"])) <= 4000 for call in gateway.calls))
        self.assertTrue(any("Section 1" in str(call["text"]) for call in gateway.calls))

    def test_telegram_publisher_does_not_truncate_long_unbroken_tokens(self) -> None:
        gateway = _FakeTelegramGateway()
        publisher = TelegramBotPublisher(gateway=gateway)

        long_token = "a" * 4505
        _external_id, payload = publisher.publish(
            channel="@news",
            html=(
                "<article><h1>Title</h1>"
                "<h2>Section</h2>"
                f"<p>{long_token}</p>"
                "</article>"
            ),
            scheduled_for=None,
        )

        self.assertGreaterEqual(len(gateway.calls), 2)
        sent_text = "\n".join(str(call["text"]) for call in gateway.calls)
        self.assertNotIn("…", sent_text)
        self.assertIn("Section", sent_text)
        self.assertTrue(payload["article_text_chars"] >= len(long_token))

    def test_telegram_publisher_uses_binary_sendphoto_for_data_uri(self) -> None:
        gateway = _FakeTelegramGateway(return_message_ids=True)
        publisher = TelegramBotPublisher(gateway=gateway)

        external_id, payload = publisher.publish(
            channel="@my_channel",
            html=(
                "<article><h1>Title</h1>"
                "<p>Lead paragraph.</p>"
                "<figure><img src=\"data:image/png;base64,ZmFrZS1ieXRlcy0x\" alt=\"Cover\" /></figure>"
                "<h2>Section A</h2><p>Body paragraph.</p></article>"
            ),
            scheduled_for=None,
        )

        self.assertEqual(external_id, "1")
        self.assertEqual(len(gateway.photo_calls), 1)
        self.assertIsInstance(gateway.photo_calls[0]["photo"], bytes)
        self.assertEqual(gateway.photo_calls[0]["file_name"], "cover.png")
        self.assertTrue(bool(payload["photo_sent"]))
        self.assertEqual(payload["image_delivery_kind"], "data_uri")

    def test_telegram_publisher_falls_back_to_text_only_when_data_uri_invalid(self) -> None:
        gateway = _FakeTelegramGateway()
        publisher = TelegramBotPublisher(gateway=gateway)

        _external_id, payload = publisher.publish(
            channel="@news",
            html=(
                "<article><h1>Title</h1>"
                "<p>Lead paragraph.</p>"
                "<figure><img src=\"data:image/png;base64,%%invalid%%\" alt=\"Cover\" /></figure>"
                "<h2>Section A</h2><p>Body paragraph.</p></article>"
            ),
            scheduled_for=None,
        )

        self.assertEqual(len(gateway.photo_calls), 0)
        self.assertGreaterEqual(len(gateway.calls), 1)
        self.assertIn("Title", str(gateway.calls[0]["text"]))
        self.assertEqual(payload["publisher_branch"], "image_fallback_to_text")
        self.assertEqual(payload["image_fallback_reason"], "IMAGE_PAYLOAD_UNSUPPORTED")

    def test_telegram_publisher_resume_skips_already_sent_photo_and_chunks(self) -> None:
        long_words_one = "alpha " * 2500
        long_words_two = "beta " * 2500
        html = (
            "<article><h1>Title</h1>"
            "<p>Lead paragraph.</p>"
            "<figure><img src=\"https://picsum.photos/seed/resume/1600/900\" alt=\"Cover\" /></figure>"
            "<h2>Section 1</h2>"
            f"<p>{long_words_one}</p>"
            "<h2>Section 2</h2>"
            f"<p>{long_words_two}</p>"
            "</article>"
        )

        failing_gateway = _FailingTelegramGateway(fail_on_message_call=2)
        failing_publisher = TelegramBotPublisher(gateway=failing_gateway)

        with self.assertRaises(ExternalDependencyError) as context:
            failing_publisher.publish(channel="@news", html=html, scheduled_for=None)

        error_payload = context.exception.details.get("publisher_payload_json")
        self.assertIsInstance(error_payload, dict)
        payload_dict = dict(error_payload or {})
        self.assertTrue(bool(payload_dict.get("photo_sent")))
        sent_chunk_indices = payload_dict.get("sent_chunk_indices") or []
        self.assertIn(0, sent_chunk_indices)
        first_sent_chunk = str(failing_gateway.calls[0]["text"])

        retry_gateway = _FakeTelegramGateway(return_message_ids=True)
        retry_publisher = TelegramBotPublisher(gateway=retry_gateway)
        external_id, retry_payload = retry_publisher.publish(
            channel="@news",
            html=html,
            scheduled_for=None,
            resume_payload_json=payload_dict,
        )

        self.assertIsNotNone(external_id)
        self.assertEqual(len(retry_gateway.photo_calls), 0)
        self.assertGreaterEqual(len(retry_gateway.calls), 1)
        self.assertTrue(all(str(call["text"]) != first_sent_chunk for call in retry_gateway.calls))
        self.assertTrue(bool(retry_payload.get("photo_sent")))
        self.assertGreaterEqual(int(retry_payload.get("parts_sent", 0)), 2)
    def test_telegram_publisher_rejects_invite_link_channel(self) -> None:
        gateway = _FakeTelegramGateway()
        publisher = TelegramBotPublisher(gateway=gateway)

        with self.assertRaises(ValidationError) as context:
            publisher.publish(
                channel="https://t.me/+drOUmIKjPO1jZjEy",
                html="<article><p>Hello</p></article>",
                scheduled_for=None,
            )

        self.assertEqual(context.exception.code, "PUBLISH_CHANNEL_INVITE_LINK_UNSUPPORTED")

    def test_telegram_publisher_unescapes_escaped_html_before_stripping(self) -> None:
        gateway = _FakeTelegramGateway()
        publisher = TelegramBotPublisher(gateway=gateway)

        _external_id, payload = publisher.publish(
            channel="@news",
            html="&lt;h1&gt;Title&lt;/h1&gt;&lt;p&gt;Hello&lt;/p&gt;",
            scheduled_for=None,
        )

        self.assertEqual(len(gateway.calls), 1)
        text = str(gateway.calls[0]["text"])
        self.assertIn("Title", text)
        self.assertIn("Hello", text)
        self.assertNotIn("<h1>", text)

    def test_telegram_publisher_http_400_is_non_retryable(self) -> None:
        publisher = TelegramBotPublisher(bot_token="123:abc")
        http_error = HTTPError(
            url="https://api.telegram.org/bot123:abc/sendMessage",
            code=400,
            msg="Bad Request",
            hdrs={},
            fp=BytesIO(b'{"ok":false,"description":"Bad Request: chat not found"}'),
        )

        with patch("post_bot.infrastructure.external.telegram_publisher.urlopen", side_effect=http_error):
            with self.assertRaises(ExternalDependencyError) as context:
                publisher.publish(
                    channel="@news",
                    html="<article><h1>Title</h1><p>Hello</p></article>",
                    scheduled_for=None,
                )

        error = context.exception
        self.assertEqual(error.code, "TELEGRAM_HTTP_ERROR")
        self.assertFalse(error.retryable)
        self.assertEqual(error.details.get("status"), 400)
        self.assertEqual(error.details.get("method"), "sendMessage")
        self.assertIn("chat not found", str(error.details.get("body")))

    def test_telegram_publisher_http_503_is_retryable(self) -> None:
        publisher = TelegramBotPublisher(bot_token="123:abc")
        http_error = HTTPError(
            url="https://api.telegram.org/bot123:abc/sendMessage",
            code=503,
            msg="Service Unavailable",
            hdrs={},
            fp=BytesIO(b'{"ok":false,"description":"Service unavailable"}'),
        )

        with patch("post_bot.infrastructure.external.telegram_publisher.urlopen", side_effect=http_error):
            with self.assertRaises(ExternalDependencyError) as context:
                publisher.publish(
                    channel="@news",
                    html="<article><h1>Title</h1><p>Hello</p></article>",
                    scheduled_for=None,
                )

        error = context.exception
        self.assertEqual(error.code, "TELEGRAM_HTTP_ERROR")
        self.assertTrue(error.retryable)
        self.assertEqual(error.details.get("status"), 503)

    def test_telegram_publisher_network_error_exposes_original_exception(self) -> None:
        publisher = TelegramBotPublisher(bot_token="123:abc")

        with patch(
            "post_bot.infrastructure.external.telegram_publisher.urlopen",
            side_effect=URLError("Temporary failure in name resolution"),
        ):
            with self.assertRaises(ExternalDependencyError) as context:
                publisher.publish(
                    channel="@news",
                    html="<article><h1>Title</h1><p>Hello</p></article>",
                    scheduled_for=None,
                )

        error = context.exception
        self.assertEqual(error.code, "TELEGRAM_NETWORK_ERROR")
        self.assertTrue(error.retryable)
        self.assertEqual(error.details.get("method"), "sendMessage")
        self.assertIsNone(error.details.get("status"))
        self.assertIsNone(error.details.get("body"))
        self.assertEqual(error.details.get("exception_type"), "URLError")
        self.assertIn("URLError", str(error.details.get("exception_repr")))
        self.assertIn("Temporary failure", str(error.details.get("reason")))

    def test_telegram_publisher_timeout_error_detected_from_urlerror_reason(self) -> None:
        publisher = TelegramBotPublisher(bot_token="123:abc")

        with patch(
            "post_bot.infrastructure.external.telegram_publisher.urlopen",
            side_effect=URLError(TimeoutError("read timed out")),
        ):
            with self.assertRaises(ExternalDependencyError) as context:
                publisher.publish(
                    channel="@news",
                    html="<article><h1>Title</h1><p>Hello</p></article>",
                    scheduled_for=None,
                )

        error = context.exception
        self.assertEqual(error.code, "TELEGRAM_TIMEOUT")
        self.assertTrue(error.retryable)
        self.assertEqual(error.details.get("method"), "sendMessage")
        self.assertIsNone(error.details.get("status"))
        self.assertIsNone(error.details.get("body"))
        self.assertEqual(error.details.get("reason_type"), "TimeoutError")
        self.assertEqual(error.details.get("exception_type"), "URLError")


if __name__ == "__main__":
    unittest.main()











