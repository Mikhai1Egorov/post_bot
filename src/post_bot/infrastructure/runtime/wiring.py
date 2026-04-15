"""Runtime composition root for worker and maintenance services."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path

from post_bot.application.ports import (
    ArtifactStoragePort,
    LLMClientPort,
    PublisherPort,
    ResearchClientPort,
)
from post_bot.application.use_cases.claim_next_task import ClaimNextTaskUseCase
from post_bot.application.use_cases.cleanup_non_final_artifacts import CleanupNonFinalArtifactsUseCase
from post_bot.application.use_cases.execute_claimed_task import ExecuteClaimedTaskUseCase
from post_bot.application.use_cases.heartbeat_task_lease import HeartbeatTaskLeaseUseCase
from post_bot.application.use_cases.expire_approval_batches import ExpireApprovalBatchesUseCase
from post_bot.application.use_cases.publish_task import PublishTaskUseCase
from post_bot.application.use_cases.recover_stale_tasks import RecoverStaleTasksUseCase
from post_bot.application.use_cases.run_maintenance_cycle import RunMaintenanceCycleUseCase
from post_bot.application.use_cases.run_task_generation import RunTaskGenerationUseCase
from post_bot.application.use_cases.run_task_rendering import RunTaskRenderingUseCase
from post_bot.application.use_cases.run_worker_cycle import RunWorkerCycleUseCase
from post_bot.application.use_cases.select_expirable_approval_batches import SelectExpirableApprovalBatchesUseCase
from post_bot.application.use_cases.select_recoverable_stale_tasks import SelectRecoverableStaleTasksUseCase
from post_bot.domain.models import TaskResearchSource
from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.infrastructure.db.mysql_uow import build_mysql_uow
from post_bot.infrastructure.external import (
    LocalArtifactPublisher,
    OpenAILLMClient,
    OpenAIResearchClient,
    TelegramBotPublisher,
)
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
    """Explicitly fails when OpenAI token is not configured."""

    model_name: str = "unconfigured"

    def collect(
        self,
        *,
        title: str,
        keywords: str,
    ) -> list[TaskResearchSource]:
        _ = (title, keywords)
        raise ExternalDependencyError(
            code="OPENAI_API_KEY_REQUIRED",
            message="OPENAI_API_KEY is required for research stage.",
            retryable=False,
        )


class UnconfiguredLLMClient:
    """Explicitly fails when OpenAI token is not configured."""

    def generate(self, *, model_name: str, prompt: str, response_language: str) -> str:
        _ = (model_name, prompt, response_language)
        raise ExternalDependencyError(
            code="OPENAI_API_KEY_REQUIRED",
            message="OPENAI_API_KEY is required for generation stage.",
            retryable=False,
        )


@dataclass(slots=True, frozen=True)
class RuntimeWiring:
    """Resolved runtime dependencies with explicit ownership."""

    uow: UnitOfWork
    artifact_storage: ArtifactStoragePort
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
        uow=build_mysql_uow(
            host=config.db_host,
            port=config.db_port,
            user=config.db_user,
            password=config.db_password,
            database=config.db_name,
        ),
        artifact_storage=LocalFileStorage(storage_root),
        research_client=research_client or _build_research_client(config),
        llm_client=llm_client or _build_llm_client(config),
        publisher=publisher or _build_publisher(config),
    )


def _build_research_client(config: AppConfig) -> ResearchClientPort:
    if not config.openai_api_key:
        return UnconfiguredResearchClient()
    return OpenAIResearchClient(
        api_key=config.openai_api_key,
        model_name=config.openai_research_model,
        timeout_seconds=config.outbound_timeout_seconds,
    )


def _build_llm_client(config: AppConfig) -> LLMClientPort:
    if not config.openai_api_key:
        return UnconfiguredLLMClient()
    return OpenAILLMClient(
        api_key=config.openai_api_key,
        timeout_seconds=config.outbound_timeout_seconds,
    )


def _build_publisher(config: AppConfig) -> PublisherPort:
    if config.telegram_bot_token:
        return TelegramBotPublisher(
            bot_token=config.telegram_bot_token,
            timeout_seconds=config.outbound_timeout_seconds,
        )
    return LocalArtifactPublisher()


def build_worker_runtime(*, wiring: RuntimeWiring, logger: logging.Logger) -> WorkerRuntime:
    generation = RunTaskGenerationUseCase(
        uow=wiring.uow,
        preparation=PreparationModule(),
        research=ResearchModule(wiring.research_client),
        prompt_resolver=PromptResolverModule(),
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
    heartbeat_lease = HeartbeatTaskLeaseUseCase(
        uow=wiring.uow,
        logger=logger.getChild("lease_heartbeat"),
    )
    execute = ExecuteClaimedTaskUseCase(
        run_generation=generation,
        run_rendering=rendering,
        publish_task=publish,
        logger=logger.getChild("execute"),
        heartbeat_task_lease=heartbeat_lease,
    )
    claim = ClaimNextTaskUseCase(uow=wiring.uow, logger=logger.getChild("claim"))
    recover_stale = RecoverStaleTasksUseCase(uow=wiring.uow, logger=logger.getChild("recover_stale_tasks"))
    cycle = RunWorkerCycleUseCase(
        claim_next_task=claim,
        execute_claimed_task=execute,
        logger=logger.getChild("cycle"),
        recover_stale_tasks=recover_stale,
    )
    return WorkerRuntime(run_worker_cycle=cycle, logger=logger)


def build_maintenance_runtime(*, wiring: RuntimeWiring, logger: logging.Logger) -> MaintenanceRuntime:
    recover = RecoverStaleTasksUseCase(uow=wiring.uow, logger=logger.getChild("recover_stale_tasks"))
    select_recoverable = SelectRecoverableStaleTasksUseCase(
        uow=wiring.uow,
        logger=logger.getChild("select_recoverable_stale_tasks"),
    )
    select_expirable = SelectExpirableApprovalBatchesUseCase(
        uow=wiring.uow,
        logger=logger.getChild("select_expirable_approval_batches"),
    )
    expire = ExpireApprovalBatchesUseCase(uow=wiring.uow, logger=logger.getChild("expire_approval_batches"))
    cleanup = CleanupNonFinalArtifactsUseCase(
        uow=wiring.uow,
        artifact_storage=wiring.artifact_storage,
        logger=logger.getChild("cleanup_non_final_artifacts"),
    )
    cycle = RunMaintenanceCycleUseCase(
        recover_stale_tasks=recover,
        select_recoverable_stale_tasks=select_recoverable,
        select_expirable_approval_batches=select_expirable,
        expire_approval_batches=expire,
        cleanup_non_final_artifacts=cleanup,
        logger=logger.getChild("cycle"),
    )
    return MaintenanceRuntime(run_maintenance_cycle=cycle, logger=logger)
