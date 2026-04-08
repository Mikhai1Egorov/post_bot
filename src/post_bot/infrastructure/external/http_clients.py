"""HTTP adapters for external research, generation, and publishing services."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from post_bot.application.ports import LLMClientPort, PublisherPort, ResearchClientPort
from post_bot.domain.models import TaskResearchSource
from post_bot.shared.errors import ExternalDependencyError

def _post_json(
    *,
    url: str,
    payload: dict[str, Any],
    api_token: str | None,
    timeout_seconds: float,
    code_prefix: str,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"

    request = Request(url=url, data=body, headers=headers, method="POST")

    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # nosec: B310
            status_code = int(getattr(response, "status", 200) or 200)
            raw_body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        retryable = int(getattr(exc, "code", 0) or 0) >= 500
        raise ExternalDependencyError(
            code=f"{code_prefix}_HTTP_ERROR",
            message="External HTTP service returned an error.",
            details={"url": url, "status_code": getattr(exc, "code", None), "reason": str(exc)},
            retryable=retryable,
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise ExternalDependencyError(
            code=f"{code_prefix}_NETWORK_ERROR",
            message="External HTTP service is unreachable.",
            details={"url": url, "reason": str(exc)},
            retryable=True,
        ) from exc

    if status_code >= 400:
        raise ExternalDependencyError(
            code=f"{code_prefix}_HTTP_ERROR",
            message="External HTTP service returned an error.",
            details={"url": url, "status_code": status_code, "body": raw_body[:1000]},
            retryable=status_code >= 500,
        )

    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise ExternalDependencyError(
            code=f"{code_prefix}_RESPONSE_NOT_JSON",
            message="External service response is not valid JSON.",
            details={"url": url},
            retryable=False,
        ) from exc

    if not isinstance(parsed, dict):
        raise ExternalDependencyError(
            code=f"{code_prefix}_RESPONSE_INVALID",
            message="External service response must be a JSON object.",
            details={"url": url, "response_type": type(parsed).__name__},
            retryable=False,
        )

    return parsed


def _parse_iso_datetime(value: str, *, code_prefix: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ExternalDependencyError(
            code=f"{code_prefix}_RESPONSE_INVALID",
            message="Datetime value has invalid ISO format.",
            details={"value": value},
            retryable=False,
        ) from exc

    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(UTC).replace(tzinfo=None)


class HttpResearchClient(ResearchClientPort):
    """Research adapter using HTTP JSON endpoint."""

    def __init__(self, *, endpoint_url: str, api_token: str | None = None, timeout_seconds: float = 15.0) -> None:
        self._endpoint_url = endpoint_url
        self._api_token = api_token
        self._timeout_seconds = timeout_seconds

    def collect(
        self,
        *,
        topic: str,
        keywords: str,
        time_range: str,
        search_language: str,
    ) -> list[TaskResearchSource]:
        response = _post_json(
            url=self._endpoint_url,
            payload={
                "topic": topic,
                "keywords": keywords,
                "time_range": time_range,
                "search_language": search_language,
            },
            api_token=self._api_token,
            timeout_seconds=self._timeout_seconds,
            code_prefix="RESEARCH",
        )

        sources_raw = response.get("sources")
        if not isinstance(sources_raw, list):
            raise ExternalDependencyError(
                code="RESEARCH_RESPONSE_INVALID",
                message="Research response must contain 'sources' list.",
                details={"response_keys": list(response.keys())},
                retryable=False,
            )

        result: list[TaskResearchSource] = []
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
                published_at = _parse_iso_datetime(published_at_raw, code_prefix="RESEARCH")
            else:
                raise ExternalDependencyError(
                    code="RESEARCH_RESPONSE_INVALID",
                    message="Research published_at must be ISO string or null.",
                    details={"index": index},
                    retryable=False,
                )

            result.append(
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

        return result


class HttpLLMClient(LLMClientPort):
    """LLM adapter using HTTP JSON endpoint."""

    def __init__(self, *, endpoint_url: str, api_token: str | None = None, timeout_seconds: float = 30.0) -> None:
        self._endpoint_url = endpoint_url
        self._api_token = api_token
        self._timeout_seconds = timeout_seconds

    def generate(self, *, model_name: str, prompt: str, response_language: str) -> str:
        response = _post_json(
            url=self._endpoint_url,
            payload={
                "model_name": model_name,
                "prompt": prompt,
                "response_language": response_language,
            },
            api_token=self._api_token,
            timeout_seconds=self._timeout_seconds,
            code_prefix="LLM",
        )
        text = response.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ExternalDependencyError(
                code="LLM_RESPONSE_INVALID",
                message="LLM response must contain non-empty 'text'.",
                details={"response_keys": list(response.keys())},
                retryable=False,
            )
        return text


class HttpPublisher(PublisherPort):
    """Publisher adapter using HTTP JSON endpoint."""

    def __init__(self, *, endpoint_url: str, api_token: str | None = None, timeout_seconds: float = 15.0) -> None:
        self._endpoint_url = endpoint_url
        self._api_token = api_token
        self._timeout_seconds = timeout_seconds

    def publish(
        self,
        *,
        channel: str,
        html: str,
        scheduled_for: datetime | None,
    ) -> tuple[str | None, dict[str, Any] | None]:
        response = _post_json(
            url=self._endpoint_url,
            payload={
                "channel": channel,
                "html": html,
                "scheduled_for": scheduled_for.replace(microsecond=0).isoformat() if scheduled_for else None,
            },
            api_token=self._api_token,
            timeout_seconds=self._timeout_seconds,
            code_prefix="PUBLISH",
        )

        external_message_id = response.get("external_message_id")
        if external_message_id is not None and not isinstance(external_message_id, str):
            raise ExternalDependencyError(
                code="PUBLISH_RESPONSE_INVALID",
                message="Publisher external_message_id must be string or null.",
                details={"response_keys": list(response.keys())},
                retryable=False,
            )

        payload = response.get("payload")
        if payload is not None and not isinstance(payload, dict):
            raise ExternalDependencyError(
                code="PUBLISH_RESPONSE_INVALID",
                message="Publisher payload must be object or null.",
                details={"response_keys": list(response.keys())},
                retryable=False,
            )

        return external_message_id, payload