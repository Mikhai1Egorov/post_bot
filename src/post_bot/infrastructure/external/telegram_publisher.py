"""Telegram publisher adapter for channel posting via Bot API."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
import hashlib
import json
import re
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from post_bot.application.ports import PublisherPort
from post_bot.infrastructure.external.telegram_delivery import TelegramDeliveryProjector
from post_bot.shared.errors import AppError, ExternalDependencyError, ValidationError


class _TelegramMessageGateway(Protocol):
    def send_message(
        self,
        *,
        chat_id: int | str,
        text: str,
        reply_markup: dict[str, object] | None = None,
    ) -> dict[str, Any] | None: ...

    def send_photo(
        self,
        *,
        chat_id: int | str,
        photo: str | bytes,
        caption: str | None = None,
        file_name: str | None = None,
    ) -> dict[str, Any] | None: ...


class _HttpTelegramMessageGateway:
    def __init__(self, *, bot_token: str, timeout_seconds: float) -> None:
        token = bot_token.strip()
        if not token:
            raise ValidationError(code="TELEGRAM_BOT_TOKEN_REQUIRED", message="Telegram bot token is required.")
        self._api_base = f"https://api.telegram.org/bot{token}"
        self._timeout_seconds = timeout_seconds

    def send_message(
        self,
        *,
        chat_id: int | str,
        text: str,
        reply_markup: dict[str, object] | None = None,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        result = self._request_json("sendMessage", payload)
        if isinstance(result, dict):
            return result
        return None

    def send_photo(
        self,
        *,
        chat_id: int | str,
        photo: str | bytes,
        caption: str | None = None,
        file_name: str | None = None,
    ) -> dict[str, Any] | None:
        if isinstance(photo, bytes):
            fields: dict[str, str] = {"chat_id": str(chat_id)}
            if caption:
                fields["caption"] = caption
            boundary = f"----PostBotBoundary{uuid4().hex}"
            body = _encode_multipart(
                fields=fields,
                file_field="photo",
                file_name=file_name or "cover.png",
                file_bytes=photo,
                boundary=boundary,
            )
            result = self._request_multipart("sendPhoto", body=body, boundary=boundary)
            if isinstance(result, dict):
                return result
            return None

        payload: dict[str, Any] = {"chat_id": chat_id, "photo": photo}
        if caption:
            payload["caption"] = caption
        result = self._request_json("sendPhoto", payload)
        if isinstance(result, dict):
            return result
        return None

    def _request_json(self, method: str, payload: dict[str, Any]) -> Any:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            url=f"{self._api_base}/{method}",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        return self._open_and_parse(method=method, request=request)

    def _request_multipart(self, method: str, *, body: bytes, boundary: str) -> Any:
        request = Request(
            url=f"{self._api_base}/{method}",
            data=body,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        return self._open_and_parse(method=method, request=request)

    def _open_and_parse(self, *, method: str, request: Request) -> Any:
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            status = int(getattr(exc, "code", 0) or 0)
            reason = str(getattr(exc, "reason", ""))
            body = self._read_http_error_body(exc)
            retry_after_seconds = self._read_retry_after_seconds(exc)
            request_id = self._read_request_id(exc)
            retryable = status == 429 or status >= 500
            raise ExternalDependencyError(
                code="TELEGRAM_HTTP_ERROR",
                message="Telegram HTTP request failed.",
                details={
                    "status": status,
                    "reason": reason,
                    "method": method,
                    "body": body[:1000] if body else None,
                    "reason_type": type(getattr(exc, "reason", None)).__name__ if getattr(exc, "reason", None) is not None else None,
                    "exception_type": type(exc).__name__,
                    "exception_repr": repr(exc),
                    "retry_after_seconds": retry_after_seconds,
                    "request_id": request_id,
                    "timeout_seconds": self._timeout_seconds,
                },
                retryable=retryable,
            ) from exc
        except URLError as exc:
            reason_obj = getattr(exc, "reason", None)
            reason = str(reason_obj if reason_obj is not None else exc)
            reason_lower = reason.casefold()
            reason_type = type(reason_obj).__name__ if reason_obj is not None else None
            exception_type = type(exc).__name__
            is_timeout = isinstance(reason_obj, TimeoutError) or "timed out" in reason_lower or "timeout" in reason_lower
            code = "TELEGRAM_TIMEOUT" if is_timeout else "TELEGRAM_NETWORK_ERROR"
            message = "Telegram request timed out." if is_timeout else "Telegram network request failed."
            raise ExternalDependencyError(
                code=code,
                message=message,
                details={
                    "status": None,
                    "body": None,
                    "reason": reason,
                    "reason_type": reason_type,
                    "exception_type": exception_type,
                    "exception_repr": repr(exc),
                    "method": method,
                    "timeout_seconds": self._timeout_seconds,
                },
                retryable=True,
            ) from exc
        except TimeoutError as exc:
            raise ExternalDependencyError(
                code="TELEGRAM_TIMEOUT",
                message="Telegram request timed out.",
                details={
                    "status": None,
                    "body": None,
                    "reason": str(exc),
                    "reason_type": type(exc).__name__,
                    "exception_type": type(exc).__name__,
                    "exception_repr": repr(exc),
                    "method": method,
                    "timeout_seconds": self._timeout_seconds,
                },
                retryable=True,
            ) from exc

        try:
            response_data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExternalDependencyError(
                code="TELEGRAM_RESPONSE_PARSE_ERROR",
                message="Failed to parse Telegram response.",
                retryable=False,
            ) from exc

        if not isinstance(response_data, dict):
            raise ExternalDependencyError(
                code="TELEGRAM_RESPONSE_INVALID",
                message="Telegram response must be JSON object.",
                details={"method": method},
                retryable=False,
            )

        if not bool(response_data.get("ok")):
            raise ExternalDependencyError(
                code="TELEGRAM_API_ERROR",
                message="Telegram API returned error.",
                details={"method": method, "response": response_data},
                retryable=False,
            )

        return response_data.get("result")

    @staticmethod
    def _read_http_error_body(error: HTTPError) -> str:
        try:
            payload = error.read()
        except Exception:  # noqa: BLE001
            return ""
        if not payload:
            return ""
        try:
            return payload.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _read_retry_after_seconds(error: HTTPError) -> float | None:
        headers = getattr(error, "headers", None)
        if headers is None:
            return None
        try:
            raw_value = headers.get("Retry-After")
        except Exception:  # noqa: BLE001
            return None
        if raw_value is None:
            return None
        try:
            parsed = float(str(raw_value).strip())
        except ValueError:
            return None
        if parsed <= 0:
            return None
        return parsed

    @staticmethod
    def _read_request_id(error: HTTPError) -> str | None:
        headers = getattr(error, "headers", None)
        if headers is None:
            return None
        try:
            raw_value = headers.get("x-request-id")
        except Exception:  # noqa: BLE001
            return None
        if raw_value is None:
            return None
        value = str(raw_value).strip()
        return value or None


class TelegramBotPublisher(PublisherPort):
    """Publishes rendered content to Telegram channel/chat via bot token."""

    _TEXT_LIMIT = 4000
    _PHOTO_CAPTION_SAFE_LIMIT = 900
    _DATA_URI_PATTERN = re.compile(r"^data:(?P<mime>[\w.+-]+\/[\w.+-]+);base64,(?P<data>[A-Za-z0-9+/=\s]+)$", re.IGNORECASE)

    def __init__(
        self,
        *,
        bot_token: str | None = None,
        timeout_seconds: float = 15.0,
        gateway: _TelegramMessageGateway | None = None,
    ) -> None:
        if gateway is not None:
            self._gateway = gateway
        else:
            token = (bot_token or "").strip()
            if not token:
                raise ValidationError(
                    code="TELEGRAM_BOT_TOKEN_REQUIRED",
                    message="Telegram bot token is required.",
                )
            self._gateway = _HttpTelegramMessageGateway(bot_token=token, timeout_seconds=timeout_seconds)

        self._delivery_projector = TelegramDeliveryProjector(
            text_limit=self._TEXT_LIMIT,
            caption_safe_limit=self._PHOTO_CAPTION_SAFE_LIMIT,
        )

    def publish(
        self,
        *,
        channel: str,
        html: str,
        scheduled_for: datetime | None,
        resume_payload_json: dict[str, Any] | None = None,
    ) -> tuple[str | None, dict[str, Any] | None]:
        chat_id = self._resolve_chat_id(channel)
        delivery = self._delivery_projector.project(html=html)

        if delivery.image_url is None and not delivery.article_chunks:
            raise ValidationError(
                code="PUBLISH_TEXT_EMPTY",
                message="Cannot publish empty content.",
                details={"channel": channel},
            )

        delivery_projection_hash = self._build_delivery_projection_hash(html=html)
        external_message_id: str | None = None
        sent_parts = 0
        photo_sent = False
        image_kind = "none"
        photo_fallback_reason: str | None = None
        message_delivery = delivery
        publisher_branch = "text_only"
        resolved_photo: tuple[str | bytes, str | None, str] | None = None
        if delivery.image_url is not None:
            resolved_photo = self._resolve_photo_payload(delivery.image_url)
            if resolved_photo is not None:
                image_kind = resolved_photo[2]
                publisher_branch = "send_photo_then_messages"
            else:
                photo_fallback_reason = "IMAGE_PAYLOAD_UNSUPPORTED"
                publisher_branch = "image_fallback_to_text"
                message_delivery = self._delivery_projector.project(html=self._strip_first_image_tag(html))

        resume_photo_sent, resume_sent_chunk_indices, resume_external_message_id = self._extract_resume_progress(
            resume_payload_json=resume_payload_json,
            delivery_projection_hash=delivery_projection_hash,
        )
        if resume_external_message_id is not None:
            external_message_id = resume_external_message_id
        sent_chunk_indices = {idx for idx in resume_sent_chunk_indices if idx < len(message_delivery.article_chunks)}

        if resume_photo_sent:
            photo_sent = True
            sent_parts += 1
        sent_parts += len(sent_chunk_indices)

        if delivery.image_url is not None and not photo_sent:
            if resolved_photo is not None:
                photo_payload, file_name, image_kind = resolved_photo
                try:
                    response = self._gateway.send_photo(
                        chat_id=chat_id,
                        photo=photo_payload,
                        caption=delivery.cover_caption_text or None,
                        file_name=file_name,
                    )
                except AppError as error:
                    self._raise_with_progress(
                        error=error,
                        payload=self._build_publish_payload(
                            channel=channel,
                            chat_id=chat_id,
                            delivery=delivery,
                            message_delivery=message_delivery,
                            external_message_id=external_message_id,
                            scheduled_for=scheduled_for,
                            publisher_branch=publisher_branch,
                            photo_sent=photo_sent,
                            image_kind=image_kind,
                            photo_fallback_reason=photo_fallback_reason,
                            sent_parts=sent_parts,
                            sent_chunk_indices=sent_chunk_indices,
                            delivery_projection_hash=delivery_projection_hash,
                            resume_used=resume_payload_json is not None,
                        ),
                    )
                photo_external_id = self._extract_message_id(response)
                if external_message_id is None:
                    external_message_id = photo_external_id
                sent_parts += 1
                photo_sent = True
                publisher_branch = "send_photo_then_messages"
            else:
                photo_fallback_reason = "IMAGE_PAYLOAD_UNSUPPORTED"
                publisher_branch = "image_fallback_to_text"
                message_delivery = self._delivery_projector.project(html=self._strip_first_image_tag(html))
                sent_chunk_indices = {idx for idx in sent_chunk_indices if idx < len(message_delivery.article_chunks)}

        for index, chunk in enumerate(message_delivery.article_chunks):
            if not chunk.strip():
                continue
            if index in sent_chunk_indices:
                continue
            try:
                response = self._gateway.send_message(chat_id=chat_id, text=chunk)
            except AppError as error:
                self._raise_with_progress(
                    error=error,
                    payload=self._build_publish_payload(
                        channel=channel,
                        chat_id=chat_id,
                        delivery=delivery,
                        message_delivery=message_delivery,
                        external_message_id=external_message_id,
                        scheduled_for=scheduled_for,
                        publisher_branch=publisher_branch,
                        photo_sent=photo_sent,
                        image_kind=image_kind,
                        photo_fallback_reason=photo_fallback_reason,
                        sent_parts=sent_parts,
                        sent_chunk_indices=sent_chunk_indices,
                        delivery_projection_hash=delivery_projection_hash,
                        resume_used=resume_payload_json is not None,
                    ),
                )
            if external_message_id is None:
                external_message_id = self._extract_message_id(response)
            sent_chunk_indices.add(index)
            sent_parts += 1

        payload = self._build_publish_payload(
            channel=channel,
            chat_id=chat_id,
            delivery=delivery,
            message_delivery=message_delivery,
            external_message_id=external_message_id,
            scheduled_for=scheduled_for,
            publisher_branch=publisher_branch,
            photo_sent=photo_sent,
            image_kind=image_kind,
            photo_fallback_reason=photo_fallback_reason,
            sent_parts=sent_parts,
            sent_chunk_indices=sent_chunk_indices,
            delivery_projection_hash=delivery_projection_hash,
            resume_used=resume_payload_json is not None,
        )
        return external_message_id, payload

    @staticmethod
    def _build_delivery_projection_hash(*, html: str) -> str:
        return hashlib.sha256(html.encode("utf-8")).hexdigest()

    @staticmethod
    def _parse_chunk_index_set(raw_indices: object) -> set[int]:
        if not isinstance(raw_indices, list):
            return set()
        parsed: set[int] = set()
        for item in raw_indices:
            if isinstance(item, bool):
                continue
            if isinstance(item, int) and item >= 0:
                parsed.add(item)
                continue
            if isinstance(item, str) and item.isdigit():
                parsed.add(int(item))
        return parsed

    @classmethod
    def _extract_resume_progress(
        cls,
        *,
        resume_payload_json: dict[str, Any] | None,
        delivery_projection_hash: str,
    ) -> tuple[bool, set[int], str | None]:
        if not isinstance(resume_payload_json, dict):
            return False, set(), None

        resume_hash = resume_payload_json.get("delivery_projection_hash")
        if isinstance(resume_hash, str) and resume_hash and resume_hash != delivery_projection_hash:
            return False, set(), None

        external_message_id = cls._coerce_optional_str(resume_payload_json.get("external_message_id"))
        photo_sent = bool(resume_payload_json.get("photo_sent"))
        sent_chunk_indices = cls._parse_chunk_index_set(resume_payload_json.get("sent_chunk_indices"))

        progress = resume_payload_json.get("delivery_progress")
        if isinstance(progress, dict):
            photo_sent = bool(progress.get("photo_sent", photo_sent))
            progress_external = cls._coerce_optional_str(progress.get("external_message_id"))
            if progress_external is not None:
                external_message_id = progress_external
            sent_chunk_indices = cls._parse_chunk_index_set(progress.get("sent_chunk_indices"))

        return photo_sent, sent_chunk_indices, external_message_id

    @staticmethod
    def _coerce_optional_str(value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            return text or None
        if isinstance(value, int):
            return str(value)
        return None

    def _build_publish_payload(
        self,
        *,
        channel: str,
        chat_id: int | str,
        delivery: Any,
        message_delivery: Any,
        external_message_id: str | None,
        scheduled_for: datetime | None,
        publisher_branch: str,
        photo_sent: bool,
        image_kind: str,
        photo_fallback_reason: str | None,
        sent_parts: int,
        sent_chunk_indices: set[int],
        delivery_projection_hash: str,
        resume_used: bool,
    ) -> dict[str, Any]:
        sorted_chunk_indices = sorted(sent_chunk_indices)
        return {
            "delivery": "telegram_bot_api",
            "channel": channel,
            "resolved_chat_id": str(chat_id),
            "external_message_id": external_message_id,
            "image_url": delivery.image_url,
            "photo_sent": photo_sent,
            "image_delivery_kind": image_kind,
            "image_fallback_reason": photo_fallback_reason,
            "publisher_branch": publisher_branch,
            "image_input_present": delivery.image_url is not None,
            "parts_sent": sent_parts,
            "sent_chunk_indices": sorted_chunk_indices,
            "cover_caption_text": delivery.cover_caption_text,
            "cover_caption_chars": len(delivery.cover_caption_text or ""),
            "article_chunks_count": len(message_delivery.article_chunks),
            "article_text_chars": len(message_delivery.telegram_article_body_text),
            "title": message_delivery.final_title_text,
            "lead": message_delivery.article_lead_text,
            "scheduled_for": scheduled_for.replace(microsecond=0).isoformat() if scheduled_for else None,
            "scheduler": "not_enforced",
            "delivery_projection_hash": delivery_projection_hash,
            "delivery_progress": {
                "photo_sent": photo_sent,
                "sent_chunk_indices": sorted_chunk_indices,
                "external_message_id": external_message_id,
            },
            "resume_payload_used": resume_used,
        }

    @staticmethod
    def _raise_with_progress(*, error: AppError, payload: dict[str, Any]) -> None:
        if isinstance(error, ExternalDependencyError):
            details = dict(error.details)
            details["publisher_payload_json"] = payload
            raise ExternalDependencyError(
                code=error.code,
                message=error.message,
                details=details,
                retryable=error.retryable,
            ) from error
        raise error

    @classmethod
    def _resolve_photo_payload(cls, image_reference: str) -> tuple[str | bytes, str | None, str] | None:
        raw = image_reference.strip()
        if not raw:
            return None

        if raw.casefold().startswith("data:"):
            match = cls._DATA_URI_PATTERN.match(raw)
            if match is None:
                return None

            mime = match.group("mime").lower()
            b64_part = re.sub(r"\s+", "", match.group("data"))
            try:
                content = base64.b64decode(b64_part, validate=True)
            except (ValueError, binascii.Error):
                return None

            if not content:
                return None

            extension = cls._mime_to_extension(mime)
            file_name = f"cover.{extension}"
            return content, file_name, "data_uri"

        return raw, None, "url"

    @staticmethod
    def _mime_to_extension(mime: str) -> str:
        if mime == "image/jpeg":
            return "jpg"
        if mime == "image/webp":
            return "webp"
        return "png"

    @staticmethod
    def _strip_first_image_tag(html: str) -> str:
        return re.sub(r"(?is)<img[^>]*>", "", html, count=1)

    @staticmethod
    def _extract_message_id(response: dict[str, Any] | None) -> str | None:
        if not isinstance(response, dict):
            return None
        message_id = response.get("message_id")
        if message_id is None:
            return None
        if isinstance(message_id, (int, str)):
            return str(message_id)
        return None

    @staticmethod
    def _resolve_chat_id(channel: str) -> int | str:
        value = channel.strip()
        if not value:
            raise ValidationError(
                code="PUBLISH_CHANNEL_EMPTY",
                message="Publish channel is required.",
            )

        if re.fullmatch(r"-?\d+", value):
            return int(value)

        if value.startswith("@"):
            return value

        parsed_chat = TelegramBotPublisher._resolve_from_link(value)
        if parsed_chat is not None:
            return parsed_chat

        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,}", value):
            return f"@{value}"

        return value

    @staticmethod
    def _resolve_from_link(value: str) -> int | str | None:
        normalized = value
        if normalized.startswith("t.me/"):
            normalized = f"https://{normalized}"
        if normalized.startswith("telegram.me/"):
            normalized = f"https://{normalized}"

        parsed = urlparse(normalized)
        host = parsed.netloc.lower()
        if host not in {"t.me", "telegram.me", "www.t.me", "www.telegram.me"}:
            return None

        parts = [part for part in parsed.path.split("/") if part]
        if not parts:
            return None

        first = parts[0]
        if first.startswith("+") or first == "joinchat":
            raise ValidationError(
                code="PUBLISH_CHANNEL_INVITE_LINK_UNSUPPORTED",
                message="Invite links cannot be used as publish channel target.",
                details={"channel": value},
            )

        if first == "c" and len(parts) >= 2 and parts[1].isdigit():
            return int(f"-100{parts[1]}")

        username = first.lstrip("@")
        if username:
            return f"@{username}"

        return None


def _encode_multipart(
    *,
    fields: dict[str, str],
    file_field: str,
    file_name: str,
    file_bytes: bytes,
    boundary: str,
) -> bytes:
    chunks: list[bytes] = []

    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        chunks.append(value.encode("utf-8"))
        chunks.append(b"\r\n")

    chunks.append(f"--{boundary}\r\n".encode("utf-8"))
    chunks.append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{file_name}"\r\n'.encode("utf-8")
    )
    chunks.append(b"Content-Type: application/octet-stream\r\n\r\n")
    chunks.append(file_bytes)
    chunks.append(b"\r\n")

    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks)



