"""Application layer ports (outbound dependencies)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from post_bot.domain.models import ParsedExcelData, TaskResearchSource
from post_bot.shared.enums import ArtifactType, InterfaceLanguage


class FileStoragePort(Protocol):
    def save_upload(self, *, user_id: int, original_filename: str, payload: bytes) -> str: ...
    def read_bytes(self, storage_path: str) -> bytes: ...


class ArtifactStoragePort(Protocol):
    def save_task_artifact(
        self,
        *,
        task_id: int | None,
        artifact_type: ArtifactType,
        file_name: str,
        content: bytes,
    ) -> str: ...

    def delete_artifact(self, storage_path: str) -> None: ...


class ZipBuilderPort(Protocol):
    def build_zip(self, files: list[tuple[str, bytes]]) -> bytes: ...


class ExcelTaskParserPort(Protocol):
    def parse(self, payload: bytes) -> ParsedExcelData: ...


class ResearchClientPort(Protocol):
    def collect(
        self,
        *,
        title: str,
        keywords: str,
    ) -> list[TaskResearchSource]: ...


class LLMClientPort(Protocol):
    def generate(self, *, model_name: str, prompt: str, response_language: str) -> str: ...


@dataclass(slots=True, frozen=True)
class GeneratedImageAsset:
    mime_type: str | None
    content: bytes | None
    prompt_text: str
    image_url: str | None = None


class ImageClientPort(Protocol):
    def generate_cover(
        self,
        *,
        task_id: int,
        article_title: str,
        article_topic: str,
        article_lead: str | None,
    ) -> GeneratedImageAsset: ...


class PublisherPort(Protocol):
    def publish(
        self,
        *,
        channel: str,
        html: str,
        scheduled_for: datetime | None,
        resume_payload_json: dict[str, Any] | None = None,
    ) -> tuple[str | None, dict[str, Any] | None]: ...


@dataclass(slots=True, frozen=True)
class StripeCheckoutSession:
    session_id: str
    checkout_url: str


@dataclass(slots=True, frozen=True)
class StripeWebhookEvent:
    event_id: str
    event_type: str
    payload_json: dict[str, Any]
    created_unix: int | None = None


class StripePaymentPort(Protocol):
    def create_checkout_session(
        self,
        *,
        package_code: str,
        user_id: int,
        success_url: str,
        cancel_url: str,
    ) -> StripeCheckoutSession: ...

    def parse_webhook_event(
        self,
        *,
        payload_bytes: bytes,
        signature_header: str | None,
    ) -> StripeWebhookEvent: ...


@dataclass(slots=True, frozen=True)
class InstructionBundle:
    template_file_name: str
    template_bytes: bytes
    readme_file_name: str
    readme_bytes: bytes


class InstructionBundleProviderPort(Protocol):
    def load_bundle(self, *, interface_language: InterfaceLanguage) -> InstructionBundle: ...

