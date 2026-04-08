"""Runtime composition root for worker and maintenance services."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path

from post_bot.application.ports import (
    ArtifactStoragePort,
    LLMClientPort,
    PromptResourceLoaderPort,
    PublisherPort,
    ResearchClientPort,
)
from post_bot.application.use_cases.claim_next_task import ClaimNextTaskUseCase
from post_bot.application.use_cases.cleanup_non_final_artifacts import CleanupNonFinalArtifactsUseCase
from post_bot.application.use_cases.execute_claimed_task import ExecuteClaimedTaskUseCase
from post_bot.application.use_cases.publish_task import PublishTaskUseCase
from post_bot.application.use_cases.recover_stale_tasks import RecoverStaleTasksUseCase
from post_bot.application.use_cases.run_maintenance_cycle import RunMaintenanceCycleUseCase
from post_bot.application.use_cases.run_task_generation import RunTaskGenerationUseCase
from post_bot.application.use_cases.run_task_rendering import RunTaskRenderingUseCase
from post_bot.application.use_cases.run_worker_cycle import RunWorkerCycleUseCase
from post_bot.domain.models import TaskResearchSource
from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.infrastructure.db.mysql_uow import build_mysql_uow_from_dsn
from post_bot.infrastructure.external import HttpLLMClient, HttpPublisher, HttpResearchClient
from post_bot.infrastructure.prompt.file_prompt_loader import FilePromptResourceLoader
from post_bot.infrastructure.runtime.maintenance_runtime import MaintenanceRuntime
from post_bot.infrastructure.runtime.worker_runtime import WorkerRuntime
from post_bot.infrastructure.storage.local_file_storage import LocalFileStorage
from post_bot.pipeline.modules.post_processing import PostProcessingModule
from post_bot.pipeline.modules.preparation import PreparationModule
from post_bot.pipeline.modules.prompt_resolver import PromptResolverModule
from post_bot.pipeline.modules.research import ResearchModule
from post_bot.shared.config import AppConfig
from post_bot.shared.errors import ExternalDependencyError


class UnconfiguredResearchClient:
    """Explicitly fails when research adapter is not configured."""

    def collect(
        self,
        *,
        topic: str,
        keywords: str,
        time_range: str,
        search_language: str,
    ) -> list[TaskResearchSource]:
        _ = (topic, keywords, time_range, search_language)
        raise ExternalDependencyError(
            code="RESEARCH_CLIENT_NOT_CONFIGURED",
            message="Research adapter is not configured.",
            retryable=False,
        )


class UnconfiguredLLMClient:
    """Explicitly fails when LLM adapter is not configured."""

    def generate(self, *, model_name: str, prompt: str, response_language: str) -> str:
        _ = (model_name, prompt, response_language)
        raise ExternalDependencyError(
            code="LLM_CLIENT_NOT_CONFIGURED",
            message="LLM adapter is not configured.",
            retryable=False,
        )


class UnconfiguredPublisher:
    """Explicitly fails when publishing adapter is not configured."""

    def publish(
        self,
        *,
        channel: str,
        html: str,
        scheduled_for,
    ) -> tuple[str | None, dict[str, object] | None]:
        _ = (channel, html, scheduled_for)
        raise ExternalDependencyError(
            code="PUBLISHER_NOT_CONFIGURED",
            message="Publisher adapter is not configured.",
            retryable=False,
        )


@dataclass(slots=True, frozen=True)
class RuntimeWiring:
    """Resolved runtime dependencies with explicit ownership."""

    uow: UnitOfWork
    artifact_storage: ArtifactStoragePort
    prompt_loader: PromptResourceLoaderPort
    research_client: ResearchClientPort
    llm_client: LLMClientPort
    publisher: PublisherPort


def build_default_runtime_wiring(
    *,
    config: AppConfig,
    project_root: str | Path,
    data_dir: str | Path | None = None,
    research_client: ResearchClientPort | None = None,
    llm_client: LLMClientPort | None = None,
    publisher: PublisherPort | None = None,
) -> RuntimeWiring:
    root = Path(project_root)
    storage_root = Path(data_dir) if data_dir is not None else root / ".runtime_data"

    return RuntimeWiring(
        uow=build_mysql_uow_from_dsn(config.database_dsn),
        artifact_storage=LocalFileStorage(storage_root),
        prompt_loader=FilePromptResourceLoader(root),
        research_client=research_client or _build_research_client(config),
        llm_client=llm_client or _build_llm_client(config),
        publisher=publisher or _build_publisher(config),
    )


def _build_research_client(config: AppConfig) -> ResearchClientPort:
    if not config.research_api_url:
        return UnconfiguredResearchClient()
    return HttpResearchClient(
        endpoint_url=config.research_api_url,
        api_token=config.outbound_api_token,
        timeout_seconds=config.outbound_timeout_seconds,
    )


def _build_llm_client(config: AppConfig) -> LLMClientPort:
    if not config.llm_api_url:
        return UnconfiguredLLMClient()
    return HttpLLMClient(
        endpoint_url=config.llm_api_url,
        api_token=config.outbound_api_token,
        timeout_seconds=config.outbound_timeout_seconds,
    )


def _build_publisher(config: AppConfig) -> PublisherPort:
    if not config.publisher_api_url:
        return UnconfiguredPublisher()
    return HttpPublisher(
        endpoint_url=config.publisher_api_url,
        api_token=config.outbound_api_token,
        timeout_seconds=config.outbound_timeout_seconds,
    )


def build_worker_runtime(*, wiring: RuntimeWiring, logger: logging.Logger) -> WorkerRuntime:
    generation = RunTaskGenerationUseCase(
        uow=wiring.uow,
        preparation=PreparationModule(),
        research=ResearchModule(wiring.research_client),
        prompt_resolver=PromptResolverModule(loader=wiring.prompt_loader),
        llm_client=wiring.llm_client,
        logger=logger.getChild("generation"),
    )
    rendering = RunTaskRenderingUseCase(
        uow=wiring.uow,
        artifact_storage=wiring.artifact_storage,
        post_processing=PostProcessingModule(),
        logger=logger.getChild("rendering"),
    )
    publish = PublishTaskUseCase(
        uow=wiring.uow,
        publisher=wiring.publisher,
        logger=logger.getChild("publish"),
    )
    execute = ExecuteClaimedTaskUseCase(
        run_generation=generation,
        run_rendering=rendering,
        publish_task=publish,
        logger=logger.getChild("execute"),
    )
    claim = ClaimNextTaskUseCase(uow=wiring.uow, logger=logger.getChild("claim"))
    cycle = RunWorkerCycleUseCase(
        claim_next_task=claim,
        execute_claimed_task=execute,
        logger=logger.getChild("cycle"),
    )
    return WorkerRuntime(run_worker_cycle=cycle, logger=logger)


def build_maintenance_runtime(*, wiring: RuntimeWiring, logger: logging.Logger) -> MaintenanceRuntime:
    recover = RecoverStaleTasksUseCase(uow=wiring.uow, logger=logger.getChild("recover_stale_tasks"))
    cleanup = CleanupNonFinalArtifactsUseCase(
        uow=wiring.uow,
        artifact_storage=wiring.artifact_storage,
        logger=logger.getChild("cleanup_non_final_artifacts"),
    )
    cycle = RunMaintenanceCycleUseCase(
        recover_stale_tasks=recover,
        cleanup_non_final_artifacts=cleanup,
        logger=logger.getChild("cycle"),
    )
    return MaintenanceRuntime(run_maintenance_cycle=cycle, logger=logger)

