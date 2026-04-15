"""OpenAI-backed adapters for research, text generation and image generation."""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
import hashlib
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from post_bot.application.ports import GeneratedImageAsset, ImageClientPort, LLMClientPort, ResearchClientPort
from post_bot.domain.models import TaskResearchSource
from post_bot.pipeline.modules.image_prompt_builder import (
    build_editorial_image_negative_prompt,
    build_editorial_image_prompt,
)
from post_bot.shared.errors import ExternalDependencyError

_OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
_OPENAI_IMAGES_GENERATIONS_URL = "https://api.openai.com/v1/images/generations"
_ASCII_PRINTABLE_MIN = 32
_ASCII_PRINTABLE_MAX = 126


def _extract_message_text(choice: dict[str, object]) -> str:
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ExternalDependencyError(
            code="OPENAI_RESPONSE_INVALID",
            message="OpenAI response choice.message must be an object.",
            retryable=False,
        )

    content = message.get("content")
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                chunks.append(text)
        return "".join(chunks)

    raise ExternalDependencyError(
        code="OPENAI_RESPONSE_INVALID",
        message="OpenAI response message.content has unsupported type.",
        details={"content_type": type(content).__name__},
        retryable=False,
    )


def _post_chat_completion(
        *,
        api_key: str,
        model_name: str,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: float,
        max_completion_tokens: int | None = None,
) -> str:
    payload: dict[str, object] = {
        "model": model_name,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if max_completion_tokens is not None:
        payload["max_completion_tokens"] = max_completion_tokens

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    request = Request(
        url=_OPENAI_CHAT_COMPLETIONS_URL,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # nosec: B310
            status_code = int(getattr(response, "status", 200) or 200)
            raw_body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        retryable = int(getattr(exc, "code", 0) or 0) >= 500
        raise ExternalDependencyError(
            code="OPENAI_HTTP_ERROR",
            message="OpenAI API returned an HTTP error.",
            details={"status_code": getattr(exc, "code", None), "reason": str(exc)},
            retryable=retryable,
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise ExternalDependencyError(
            code="OPENAI_NETWORK_ERROR",
            message="OpenAI API is unreachable.",
            details={"reason": str(exc)},
            retryable=True,
        ) from exc

    if status_code >= 400:
        raise ExternalDependencyError(
            code="OPENAI_HTTP_ERROR",
            message="OpenAI API returned an HTTP error.",
            details={"status_code": status_code, "body": raw_body[:1000]},
            retryable=status_code >= 500,
        )

    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise ExternalDependencyError(
            code="OPENAI_RESPONSE_NOT_JSON",
            message="OpenAI API response is not valid JSON.",
            retryable=False,
        ) from exc

    if not isinstance(parsed, dict):
        raise ExternalDependencyError(
            code="OPENAI_RESPONSE_INVALID",
            message="OpenAI API response must be a JSON object.",
            details={"response_type": type(parsed).__name__},
            retryable=False,
        )

    choices = parsed.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ExternalDependencyError(
            code="OPENAI_RESPONSE_INVALID",
            message="OpenAI API response must contain non-empty choices list.",
            details={"response_keys": list(parsed.keys())},
            retryable=False,
        )

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise ExternalDependencyError(
            code="OPENAI_RESPONSE_INVALID",
            message="OpenAI API response choices item must be an object.",
            retryable=False,
        )

    text = _extract_message_text(first_choice).strip()
    if not text:
        raise ExternalDependencyError(
            code="OPENAI_RESPONSE_INVALID",
            message="OpenAI API response must contain non-empty message content.",
            retryable=False,
        )

    return text


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


def _parse_iso_datetime(value: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ExternalDependencyError(
            code="RESEARCH_RESPONSE_INVALID",
            message="published_at has invalid ISO datetime format.",
            details={"value": value},
            retryable=False,
        ) from exc

    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(UTC).replace(tzinfo=None)


class OpenAIResearchClient(ResearchClientPort):
    """Collects structured research sources using a single GPT model."""

    def __init__(self, *, api_key: str, model_name: str, timeout_seconds: float = 15.0) -> None:
        self._api_key = api_key
        self._model_name = model_name
        self._timeout_seconds = timeout_seconds

    @property
    def model_name(self) -> str:
        return self._model_name

    def collect(
            self,
            *,
            title: str,
            keywords: str,
    ) -> list[TaskResearchSource]:
        raw_text = _post_chat_completion(
            api_key=self._api_key,
            model_name=self._model_name,
            timeout_seconds=self._timeout_seconds,
            system_prompt=(
                "You are a research assistant. Return only JSON. "
                "Find concise, relevant web sources for the requested topic."
            ),
            user_prompt=(
                "Return JSON object with key 'sources'. "
                "Each source item must be object with fields: "
                "source_url (required string), source_title (optional string|null), "
                "source_language_code (optional string|null), published_at (optional ISO datetime string|null), "
                "source_payload_json (optional object|null). "
                f"title={title}; keywords={keywords}. "
                "Return max 5 items. No markdown."
            ),
        )

        normalized_text = _strip_code_fence(raw_text)
        try:
            parsed = json.loads(normalized_text)
        except json.JSONDecodeError as exc:
            raise ExternalDependencyError(
                code="RESEARCH_RESPONSE_INVALID",
                message="Research response must be valid JSON object.",
                retryable=False,
            ) from exc

        if not isinstance(parsed, dict):
            raise ExternalDependencyError(
                code="RESEARCH_RESPONSE_INVALID",
                message="Research response must be a JSON object.",
                retryable=False,
            )

        sources_raw = parsed.get("sources")
        if not isinstance(sources_raw, list):
            raise ExternalDependencyError(
                code="RESEARCH_RESPONSE_INVALID",
                message="Research response must contain 'sources' list.",
                details={"response_keys": list(parsed.keys())},
                retryable=False,
            )

        sources: list[TaskResearchSource] = []
        for index, item in enumerate(sources_raw, start=1):
            if not isinstance(item, dict):
                raise ExternalDependencyError(
                    code="RESEARCH_RESPONSE_INVALID",
                    message="Research source item must be an object.",
                    details={"index": index, "item_type": type(item).__name__},
                    retryable=False,
                )

            source_url = item.get("source_url")
            if not isinstance(source_url, str) or not source_url.strip():
                raise ExternalDependencyError(
                    code="RESEARCH_RESPONSE_INVALID",
                    message="Research source_url is required.",
                    details={"index": index},
                    retryable=False,
                )

            source_title = item.get("source_title")
            if source_title is not None and not isinstance(source_title, str):
                raise ExternalDependencyError(
                    code="RESEARCH_RESPONSE_INVALID",
                    message="Research source_title must be string or null.",
                    details={"index": index},
                    retryable=False,
                )

            source_language = item.get("source_language_code")
            if source_language is not None and not isinstance(source_language, str):
                raise ExternalDependencyError(
                    code="RESEARCH_RESPONSE_INVALID",
                    message="Research source_language_code must be string or null.",
                    details={"index": index},
                    retryable=False,
                )

            source_payload = item.get("source_payload_json")
            if source_payload is not None and not isinstance(source_payload, dict):
                raise ExternalDependencyError(
                    code="RESEARCH_RESPONSE_INVALID",
                    message="Research source_payload_json must be object or null.",
                    details={"index": index},
                    retryable=False,
                )

            published_at_raw = item.get("published_at")
            if published_at_raw is None:
                published_at = None
            elif isinstance(published_at_raw, str):
                published_at = _parse_iso_datetime(published_at_raw)
            else:
                raise ExternalDependencyError(
                    code="RESEARCH_RESPONSE_INVALID",
                    message="Research published_at must be ISO string or null.",
                    details={"index": index},
                    retryable=False,
                )

            sources.append(
                TaskResearchSource(
                    id=0,
                    task_id=0,
                    source_url=source_url.strip(),
                    source_title=source_title,
                    source_language_code=source_language,
                    published_at=published_at,
                    source_payload_json=source_payload,
                )
            )

        return sources


class OpenAILLMClient(LLMClientPort):
    """Generation adapter using the same GPT provider/token."""

    _LANGUAGE_NAME_BY_CODE: dict[str, str] = {
        "en": "English",
        "ru": "Russian",
        "es": "Spanish",
        "uk": "Ukrainian",
        "zh": "Chinese",
        "hi": "Hindi",
        "ar": "Arabic",
    }

    def __init__(self, *, api_key: str, timeout_seconds: float = 30.0) -> None:
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._max_completion_tokens = 350

    def generate(self, *, model_name: str, prompt: str, response_language: str) -> str:
        language_guardrail = self._build_language_guardrail(response_language)
        text = _post_chat_completion(
            api_key=self._api_key,
            model_name=model_name,
            timeout_seconds=self._timeout_seconds,
            system_prompt=(
                "You generate publication-ready article drafts. "
                "Follow the prompt exactly and return clean text only. "
                "Never switch away from the required output language."
            ),
            user_prompt=(
                f"{language_guardrail}\n"
                f"response_language={response_language}\n\n"
                "Follow the task prompt below exactly:\n"
                f"{prompt}"
            ),
            max_completion_tokens=self._max_completion_tokens,
        )
        if not text.strip():
            raise ExternalDependencyError(
                code="LLM_RESPONSE_INVALID",
                message="LLM response must contain non-empty text.",
                retryable=False,
            )
        return text

    def _build_language_guardrail(self, response_language: str) -> str:
        normalized = (response_language or "").strip().lower()
        language_name = self._LANGUAGE_NAME_BY_CODE.get(normalized, "English")
        return (
            f"Mandatory language rule: output the final article strictly in {language_name}. "
            "Ignore the language used in title, keywords, and research sources. "
            "Do not mix languages."
        )


class OpenAIImageClient(ImageClientPort):
    _DEFAULT_IMAGE_MIME = "image/png"
    _MIN_IMAGE_REQUEST_TIMEOUT_SECONDS = 45.0
    _MAX_IMAGE_REQUEST_ATTEMPTS = 2
    _STABILITY_IMAGE_GENERATE_URL = "https://api.stability.ai/v2beta/stable-image/generate/core"

    def __init__(
            self,
            *,
            api_key: str,
            model_name: str | None = None,
            api_key_source: str | None = None,
            timeout_seconds: float = 30.0,
    ) -> None:
        self._api_key = api_key
        # Backward-compatible constructor argument: wiring may still provide model_name.
        self._model_name = (model_name or "").strip() or None
        self._api_key_source = (api_key_source or "unknown").strip() or "unknown"
        self._api_key_fingerprint = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]
        self._timeout_seconds = timeout_seconds

    def generate_cover(
            self,
            *,
            task_id: int,
            article_title: str,
            article_topic: str,
            article_keywords: str | None = None,
            article_lead: str | None = None,
    ) -> GeneratedImageAsset:

        prompt = build_editorial_image_prompt(
            article_title=article_title,
            article_topic=article_topic,
            article_keywords=article_keywords,
            article_lead=article_lead,
        )
        negative_prompt = build_editorial_image_negative_prompt(
            article_title=article_title,
            article_topic=article_topic,
            article_keywords=article_keywords,
            article_lead=article_lead,
        )
        safe_prompt = self._force_ascii_prompt(prompt, fallback="prompt: create one original editorial image without people.")
        safe_negative_prompt = self._force_ascii_prompt(
            negative_prompt,
            fallback="person, people, human, face, portrait, text, logo, watermark",
        )
        seed = self._derive_stability_seed()

        status_code, content, transport = self._perform_generation_request(
            prompt=safe_prompt,
            negative_prompt=safe_negative_prompt,
            seed=seed,
            task_id=task_id,
        )

        if status_code >= 400:
            raise ExternalDependencyError(
                code="STABILITY_HTTP_ERROR",
                message="Stability API error",
                details={
                    "status_code": status_code,
                    "task_id": task_id,
                    "image_model": self._model_name,
                    "endpoint": self._STABILITY_IMAGE_GENERATE_URL,
                    "transport": transport,
                    "timeout_seconds": max(self._timeout_seconds, self._MIN_IMAGE_REQUEST_TIMEOUT_SECONDS),
                    "prompt_chars": len(safe_prompt),
                    "negative_prompt_chars": len(safe_negative_prompt),
                    "seed": seed,
                    "body": self._decode_text_preview(content, max_chars=1000),
                    "api_key_source": self._api_key_source,
                    "api_key_fingerprint": self._api_key_fingerprint,
                },
                retryable=status_code >= 500,
            )

        if not content:
            raise ExternalDependencyError(
                code="STABILITY_EMPTY_RESPONSE",
                message="Empty image response",
                details={"task_id": task_id, "image_model": self._model_name},
                retryable=False,
            )

        return GeneratedImageAsset(
            mime_type=self._DEFAULT_IMAGE_MIME,
            content=content,
            prompt_text=prompt,
            image_url=None,
        )

    def _perform_generation_request(
        self,
        *,
        prompt: str,
        negative_prompt: str,
        seed: int,
        task_id: int,
    ) -> tuple[int, bytes, str]:
        requests_module: Any | None = None
        requests_exception_type: Any | None = None
        try:
            import requests as _requests  # type: ignore
            requests_module = _requests
            requests_exception_type = _requests.RequestException
        except Exception:  # noqa: BLE001
            requests_module = None
            requests_exception_type = None

        timeout_seconds = max(self._timeout_seconds, self._MIN_IMAGE_REQUEST_TIMEOUT_SECONDS)
        boundary = f"----PostBotStabilityBoundary{uuid4().hex}"
        request_fields = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "seed": str(seed),
            "output_format": "png",
        }
        body = self._encode_stability_multipart(
            fields=request_fields,
            boundary=boundary,
        )
        transport = "requests" if requests_module is not None else "urllib"

        for attempt in range(1, self._MAX_IMAGE_REQUEST_ATTEMPTS + 1):
            if requests_module is not None:
                try:
                    response = requests_module.post(
                        self._STABILITY_IMAGE_GENERATE_URL,
                        headers={
                            "Authorization": f"Bearer {self._api_key}",
                            "Accept": "image/*",
                        },
                        files={name: (None, value) for name, value in request_fields.items() if value},
                        timeout=timeout_seconds,
                    )
                    return (
                        int(getattr(response, "status_code", 0) or 0),
                        bytes(getattr(response, "content", b"") or b""),
                        transport,
                    )
                except requests_exception_type as exc:  # type: ignore[misc]
                    if attempt < self._MAX_IMAGE_REQUEST_ATTEMPTS:
                        continue
                    raise ExternalDependencyError(
                        code="STABILITY_NETWORK_ERROR",
                        message="Stability API unreachable",
                        details={
                            "reason": str(exc),
                            "task_id": task_id,
                            "image_model": self._model_name,
                            "endpoint": self._STABILITY_IMAGE_GENERATE_URL,
                            "transport": transport,
                            "timeout_seconds": timeout_seconds,
                            "prompt_chars": len(prompt),
                            "negative_prompt_chars": len(negative_prompt),
                            "seed": seed,
                            "api_key_source": self._api_key_source,
                            "api_key_fingerprint": self._api_key_fingerprint,
                        },
                        retryable=True,
                    ) from exc

            try:
                request = Request(
                    url=self._STABILITY_IMAGE_GENERATE_URL,
                    data=body,
                    method="POST",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Accept": "image/*",
                        "Content-Type": f"multipart/form-data; boundary={boundary}",
                        "User-Agent": "post-bot/1.0",
                    },
                )
                with urlopen(request, timeout=timeout_seconds) as response:  # nosec: B310
                    status_code = int(getattr(response, "status", 200) or 200)
                    return status_code, response.read(), transport
            except HTTPError as exc:
                status_code = int(getattr(exc, "code", 0) or 0)
                retryable = status_code == 429 or status_code >= 500
                if retryable and attempt < self._MAX_IMAGE_REQUEST_ATTEMPTS:
                    continue

                raise ExternalDependencyError(
                    code="STABILITY_HTTP_ERROR",
                    message="Stability API error",
                    details={
                        "status_code": status_code,
                        "reason": str(getattr(exc, "reason", "")),
                        "body": self._read_http_error_body(exc)[:1000],
                        "task_id": task_id,
                        "image_model": self._model_name,
                        "endpoint": self._STABILITY_IMAGE_GENERATE_URL,
                        "transport": transport,
                        "timeout_seconds": timeout_seconds,
                        "prompt_chars": len(prompt),
                        "negative_prompt_chars": len(negative_prompt),
                        "seed": seed,
                        "api_key_source": self._api_key_source,
                        "api_key_fingerprint": self._api_key_fingerprint,
                    },
                    retryable=retryable,
                ) from exc
            except URLError as exc:
                if attempt < self._MAX_IMAGE_REQUEST_ATTEMPTS:
                    continue

                raise ExternalDependencyError(
                    code="STABILITY_NETWORK_ERROR",
                    message="Stability API unreachable",
                    details={
                        "reason": str(exc),
                        "task_id": task_id,
                        "image_model": self._model_name,
                        "endpoint": self._STABILITY_IMAGE_GENERATE_URL,
                        "transport": transport,
                        "timeout_seconds": timeout_seconds,
                        "prompt_chars": len(prompt),
                        "negative_prompt_chars": len(negative_prompt),
                        "seed": seed,
                        "api_key_source": self._api_key_source,
                        "api_key_fingerprint": self._api_key_fingerprint,
                    },
                    retryable=True,
                ) from exc
            except TimeoutError as exc:
                if attempt < self._MAX_IMAGE_REQUEST_ATTEMPTS:
                    continue
                raise ExternalDependencyError(
                    code="STABILITY_NETWORK_ERROR",
                    message="Stability API unreachable",
                    details={
                        "reason": str(exc),
                        "task_id": task_id,
                        "image_model": self._model_name,
                        "endpoint": self._STABILITY_IMAGE_GENERATE_URL,
                        "transport": transport,
                        "timeout_seconds": timeout_seconds,
                        "prompt_chars": len(prompt),
                        "negative_prompt_chars": len(negative_prompt),
                        "seed": seed,
                        "api_key_source": self._api_key_source,
                        "api_key_fingerprint": self._api_key_fingerprint,
                    },
                    retryable=True,
                ) from exc

        raise ExternalDependencyError(
            code="STABILITY_NETWORK_ERROR",
            message="Stability API unreachable",
            details={
                "task_id": task_id,
                "image_model": self._model_name,
                "endpoint": self._STABILITY_IMAGE_GENERATE_URL,
                "transport": transport,
                "timeout_seconds": timeout_seconds,
                "prompt_chars": len(prompt),
                "negative_prompt_chars": len(negative_prompt),
                "seed": seed,
                "api_key_source": self._api_key_source,
                "api_key_fingerprint": self._api_key_fingerprint,
            },
            retryable=True,
        )

    @staticmethod
    def _encode_stability_multipart(*, fields: dict[str, str], boundary: str) -> bytes:
        parts: list[bytes] = []
        for name, value in fields.items():
            if not value:
                continue
            parts.append(f"--{boundary}\r\n".encode("utf-8"))
            parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
            parts.append(value.encode("utf-8"))
            parts.append(b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        return b"".join(parts)

    @staticmethod
    def _derive_stability_seed() -> int:
        # Random seed lowers repeated-looking outputs across different tasks.
        return max(1, uuid4().int % 4_294_967_294)

    @staticmethod
    def _force_ascii_prompt(value: str, *, fallback: str) -> str:
        if not value:
            return fallback
        normalized = " ".join(value.split())
        filtered = "".join(
            ch if _ASCII_PRINTABLE_MIN <= ord(ch) <= _ASCII_PRINTABLE_MAX else " "
            for ch in normalized
        )
        filtered = " ".join(filtered.split())
        if not filtered:
            return fallback
        return filtered

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
    def _decode_text_preview(payload: bytes, *, max_chars: int) -> str | None:
        if not payload:
            return None
        try:
            text = payload.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return None
        text = text.strip()
        if not text:
            return None
        return text[:max_chars]
