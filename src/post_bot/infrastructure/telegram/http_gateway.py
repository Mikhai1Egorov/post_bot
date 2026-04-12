"""Telegram HTTP gateway adapter."""

from __future__ import annotations

import json
import time
from pathlib import PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from post_bot.infrastructure.runtime.telegram_runtime import TelegramDownloadedFile
from post_bot.shared.errors import ExternalDependencyError, ValidationError


class TelegramHttpGateway:
    """Low-level Telegram Bot API adapter over HTTPS."""

    def __init__(self, *, bot_token: str, timeout_seconds: float = 15.0) -> None:
        token = bot_token.strip()
        if not token:
            raise ValidationError(code="TELEGRAM_BOT_TOKEN_REQUIRED", message="Telegram bot token is required.")

        self._timeout_seconds = timeout_seconds
        self._api_base = f"https://api.telegram.org/bot{token}"
        self._file_base = f"https://api.telegram.org/file/bot{token}"

    def get_updates(self, *, offset: int | None, timeout_seconds: int) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout_seconds}
        if offset is not None:
            payload["offset"] = offset

        request_timeout_seconds = max(self._timeout_seconds, float(timeout_seconds) + 5.0)
        result = self._request_json(
            "getUpdates",
            payload,
            max_attempts=2,
            request_timeout_seconds=request_timeout_seconds,
        )
        if not isinstance(result, list):
            raise ExternalDependencyError(
                code="TELEGRAM_UPDATES_INVALID_RESPONSE",
                message="Telegram getUpdates returned invalid payload.",
                details={"result_type": type(result).__name__},
                retryable=False,
            )

        normalized: list[dict[str, Any]] = []
        for item in result:
            if isinstance(item, dict):
                normalized.append(item)
        return normalized

    def send_message(self, *, chat_id: int | str, text: str, reply_markup: dict[str, object] | None = None) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self._request_json(
            "sendMessage",
            payload,
            max_attempts=1,
            request_timeout_seconds=max(1.0, min(self._timeout_seconds, 5.0)),
        )

    def send_document(
        self,
        *,
        chat_id: int | str,
        file_name: str,
        payload: bytes,
        caption: str | None = None,
    ) -> None:
        fields: dict[str, str] = {"chat_id": str(chat_id)}
        if caption:
            fields["caption"] = caption

        boundary = f"----PostBotBoundary{uuid4().hex}"
        body = _encode_multipart(
            fields=fields,
            file_field="document",
            file_name=file_name,
            file_bytes=payload,
            boundary=boundary,
        )
        request = Request(
            url=f"{self._api_base}/sendDocument",
            data=body,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        self._open_and_parse(
            request,
            max_attempts=1,
            request_timeout_seconds=max(1.0, min(self._timeout_seconds, 7.0)),
        )

    def download_file(self, *, file_id: str, fallback_file_name: str | None = None) -> TelegramDownloadedFile:
        result = self._request_json(
            "getFile",
            {"file_id": file_id},
            max_attempts=2,
            request_timeout_seconds=self._timeout_seconds,
        )
        if not isinstance(result, dict):
            raise ExternalDependencyError(
                code="TELEGRAM_GET_FILE_INVALID_RESPONSE",
                message="Telegram getFile returned invalid payload.",
                details={"result_type": type(result).__name__},
                retryable=False,
            )

        file_path = result.get("file_path")
        if not isinstance(file_path, str) or not file_path:
            raise ExternalDependencyError(
                code="TELEGRAM_FILE_PATH_MISSING",
                message="Telegram getFile did not return file_path.",
                retryable=False,
            )

        file_request = Request(url=f"{self._file_base}/{file_path}", method="GET")
        payload = self._open_raw(
            file_request,
            max_attempts=2,
            request_timeout_seconds=self._timeout_seconds,
        )
        derived_name = PurePosixPath(file_path).name
        file_name = fallback_file_name or derived_name or "upload.xlsx"
        return TelegramDownloadedFile(file_name=file_name, payload=payload)

    def answer_callback_query(self, *, callback_query_id: str) -> None:
        self._request_json(
            "answerCallbackQuery",
            {"callback_query_id": callback_query_id},
            max_attempts=1,
            request_timeout_seconds=max(1.0, min(self._timeout_seconds, 5.0)),
        )

    def _request_json(
        self,
        method: str,
        payload: dict[str, Any],
        *,
        max_attempts: int,
        request_timeout_seconds: float,
    ) -> Any:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            url=f"{self._api_base}/{method}",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        response_data = self._open_and_parse(
            request,
            max_attempts=max_attempts,
            request_timeout_seconds=request_timeout_seconds,
        )
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

    def _open_and_parse(
        self,
        request: Request,
        *,
        max_attempts: int,
        request_timeout_seconds: float,
    ) -> Any:
        raw = self._open_raw(
            request,
            max_attempts=max_attempts,
            request_timeout_seconds=request_timeout_seconds,
        )
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExternalDependencyError(
                code="TELEGRAM_RESPONSE_PARSE_ERROR",
                message="Failed to parse Telegram response.",
                retryable=False,
            ) from exc

    def _open_raw(
        self,
        request: Request,
        *,
        max_attempts: int,
        request_timeout_seconds: float,
    ) -> bytes:
        is_get_updates = str(getattr(request, "full_url", "")).endswith("/getUpdates")
        endpoint = self._resolve_endpoint_name(request)

        for attempt in range(1, max_attempts + 1):
            try:
                with urlopen(request, timeout=request_timeout_seconds) as response:
                    return response.read()
            except HTTPError as exc:
                status = int(getattr(exc, "code", 0) or 0)
                reason = str(getattr(exc, "reason", ""))
                body = self._read_http_error_body(exc)
                retry_after_seconds = self._read_retry_after_seconds(exc)

                if status == 409 and is_get_updates:
                    raise ExternalDependencyError(
                        code="TELEGRAM_POLLING_CONFLICT",
                        message="Telegram getUpdates conflict: another bot instance is already polling.",
                        details={
                            "status": status,
                            "reason": reason,
                            "body": body[:1000] if body else None,
                            "endpoint": endpoint,
                            "attempt": attempt,
                            "max_attempts": max_attempts,
                        },
                        retryable=False,
                    ) from exc

                retryable = status == 429 or status >= 500
                if retryable and attempt < max_attempts:
                    time.sleep(self._retry_delay_seconds(attempt=attempt, retry_after_seconds=retry_after_seconds))
                    continue

                raise ExternalDependencyError(
                    code="TELEGRAM_HTTP_ERROR",
                    message="Telegram HTTP request failed.",
                    details={
                        "status": status,
                        "reason": reason,
                        "body": body[:1000] if body else None,
                        "endpoint": endpoint,
                        "retry_after_seconds": retry_after_seconds,
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                    },
                    retryable=retryable,
                ) from exc
            except URLError as exc:
                reason_obj = getattr(exc, "reason", None)
                reason = str(reason_obj if reason_obj is not None else exc)
                reason_type = type(reason_obj).__name__ if reason_obj is not None else None
                if attempt < max_attempts:
                    time.sleep(self._retry_delay_seconds(attempt=attempt, retry_after_seconds=None))
                    continue
                raise ExternalDependencyError(
                    code="TELEGRAM_NETWORK_ERROR",
                    message="Telegram network request failed.",
                    details={
                        "status": None,
                        "body": None,
                        "reason": reason,
                        "reason_type": reason_type,
                        "exception_type": type(exc).__name__,
                        "exception_repr": repr(exc),
                        "endpoint": endpoint,
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                    },
                    retryable=True,
                ) from exc
            except TimeoutError as exc:
                if attempt < max_attempts:
                    time.sleep(self._retry_delay_seconds(attempt=attempt, retry_after_seconds=None))
                    continue
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
                        "endpoint": endpoint,
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                    },
                    retryable=True,
                ) from exc

        raise ExternalDependencyError(
            code="TELEGRAM_NETWORK_ERROR",
            message="Telegram network request failed.",
            details={
                "reason": "request attempts exhausted",
                "endpoint": endpoint,
                "max_attempts": max_attempts,
                "timeout_seconds": request_timeout_seconds,
            },
            retryable=True,
        )

    @staticmethod
    def _resolve_endpoint_name(request: Request) -> str | None:
        url = str(getattr(request, "full_url", ""))
        if not url:
            return None
        tail = url.rsplit("/", maxsplit=1)[-1].strip()
        return tail or None

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
        return min(parsed, 10.0)

    @staticmethod
    def _retry_delay_seconds(*, attempt: int, retry_after_seconds: float | None) -> float:
        if retry_after_seconds is not None:
            return retry_after_seconds
        return min(2.0, 0.5 * attempt)


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


