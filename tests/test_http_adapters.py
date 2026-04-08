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

from post_bot.infrastructure.external.http_clients import HttpLLMClient, HttpPublisher, HttpResearchClient  # noqa: E402
from post_bot.shared.errors import ExternalDependencyError  # noqa: E402


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


class HttpAdaptersTests(unittest.TestCase):
    def test_research_client_parses_sources(self) -> None:
        client = HttpResearchClient(endpoint_url="https://research.example/api", api_token="token")

        with patch(
            "post_bot.infrastructure.external.http_clients.urlopen",
            return_value=_FakeResponse(
                payload={
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
            ),
        ) as mocked_open:
            result = client.collect(topic="AI", keywords="ai", time_range="24h", search_language="en")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].source_url, "https://example.com/a")
        self.assertEqual(result[0].published_at, datetime(2026, 4, 8, 10, 0, 0))

        request = mocked_open.call_args.args[0]
        self.assertEqual(request.full_url, "https://research.example/api")
        self.assertEqual(request.get_header("Authorization"), "Bearer token")

    def test_research_client_rejects_invalid_sources_payload(self) -> None:
        client = HttpResearchClient(endpoint_url="https://research.example/api")

        with patch(
            "post_bot.infrastructure.external.http_clients.urlopen",
            return_value=_FakeResponse(payload={"sources": "bad"}),
        ):
            with self.assertRaises(ExternalDependencyError) as context:
                client.collect(topic="AI", keywords="ai", time_range="24h", search_language="en")

        self.assertEqual(context.exception.code, "RESEARCH_RESPONSE_INVALID")
        self.assertFalse(context.exception.retryable)

    def test_llm_client_retries_on_http_5xx(self) -> None:
        client = HttpLLMClient(endpoint_url="https://llm.example/api")

        with patch(
            "post_bot.infrastructure.external.http_clients.urlopen",
            side_effect=HTTPError(
                url="https://llm.example/api",
                code=503,
                msg="Service Unavailable",
                hdrs=None,
                fp=BytesIO(b"{}"),
            ),
        ):
            with self.assertRaises(ExternalDependencyError) as context:
                client.generate(model_name="gpt-test", prompt="prompt", response_language="en")

        self.assertEqual(context.exception.code, "LLM_HTTP_ERROR")
        self.assertTrue(context.exception.retryable)

    def test_llm_client_rejects_empty_text_response(self) -> None:
        client = HttpLLMClient(endpoint_url="https://llm.example/api")

        with patch(
            "post_bot.infrastructure.external.http_clients.urlopen",
            return_value=_FakeResponse(payload={"text": "  "}),
        ):
            with self.assertRaises(ExternalDependencyError) as context:
                client.generate(model_name="gpt-test", prompt="prompt", response_language="en")

        self.assertEqual(context.exception.code, "LLM_RESPONSE_INVALID")

    def test_publisher_client_returns_external_id_and_payload(self) -> None:
        client = HttpPublisher(endpoint_url="https://publisher.example/api")

        with patch(
            "post_bot.infrastructure.external.http_clients.urlopen",
            return_value=_FakeResponse(
                payload={
                    "external_message_id": "msg-123",
                    "payload": {"provider": "x"},
                }
            ),
        ):
            external_id, payload = client.publish(
                channel="@news",
                html="<article>ok</article>",
                scheduled_for=datetime(2026, 4, 8, 12, 30, 1),
            )

        self.assertEqual(external_id, "msg-123")
        self.assertEqual(payload, {"provider": "x"})

    def test_publisher_client_retries_on_network_error(self) -> None:
        client = HttpPublisher(endpoint_url="https://publisher.example/api")

        with patch(
            "post_bot.infrastructure.external.http_clients.urlopen",
            side_effect=URLError("connection reset"),
        ):
            with self.assertRaises(ExternalDependencyError) as context:
                client.publish(channel="@news", html="<article>ok</article>", scheduled_for=None)

        self.assertEqual(context.exception.code, "PUBLISH_NETWORK_ERROR")
        self.assertTrue(context.exception.retryable)


if __name__ == "__main__":
    unittest.main()

