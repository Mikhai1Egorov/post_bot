"""Telegram HTTP gateway adapter."""

from __future__ import annotations

import json
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

        result = self._request_json("getUpdates", payload)
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

    def send_message(self, *, chat_id: int, text: str, reply_markup: dict[str, object] | None = None) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self._request_json("sendMessage", payload)

    def send_document(
        self,
        *,
        chat_id: int,
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
        self._open_and_parse(request)

    def download_file(self, *, file_id: str, fallback_file_name: str | None = None) -> TelegramDownloadedFile:
        result = self._request_json("getFile", {"file_id": file_id})
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
        payload = self._open_raw(file_request)
        derived_name = PurePosixPath(file_path).name
        file_name = fallback_file_name or derived_name or "upload.xlsx"
        return TelegramDownloadedFile(file_name=file_name, payload=payload)

    def answer_callback_query(self, *, callback_query_id: str) -> None:
        self._request_json("answerCallbackQuery", {"callback_query_id": callback_query_id})

    def _request_json(self, method: str, payload: dict[str, Any]) -> Any:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            url=f"{self._api_base}/{method}",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        response_data = self._open_and_parse(request)
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

    def _open_and_parse(self, request: Request) -> Any:
        raw = self._open_raw(request)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExternalDependencyError(
                code="TELEGRAM_RESPONSE_PARSE_ERROR",
                message="Failed to parse Telegram response.",
                retryable=False,
            ) from exc

    def _open_raw(self, request: Request) -> bytes:
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                return response.read()
        except HTTPError as exc:
            raise ExternalDependencyError(
                code="TELEGRAM_HTTP_ERROR",
                message="Telegram HTTP request failed.",
                details={"status": exc.code, "reason": str(exc.reason)},
                retryable=True,
            ) from exc
        except URLError as exc:
            raise ExternalDependencyError(
                code="TELEGRAM_NETWORK_ERROR",
                message="Telegram network request failed.",
                details={"reason": str(exc.reason)},
                retryable=True,
            ) from exc
        except TimeoutError as exc:
            raise ExternalDependencyError(
                code="TELEGRAM_TIMEOUT",
                message="Telegram request timed out.",
                retryable=True,
            ) from exc


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
