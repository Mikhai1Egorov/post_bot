"""Runtime composition root for Telegram transport handlers."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger
from pathlib import Path

from post_bot.application.ports import (
    ExcelTaskParserPort,
    FileStoragePort,
    InstructionBundleProviderPort,
    PublisherPort,
)
from post_bot.application.use_cases.build_approval_batch import BuildApprovalBatchUseCase
from post_bot.application.use_cases.create_tasks import TaskCreationUseCase
from post_bot.application.use_cases.download_approval_batch import DownloadApprovalBatchUseCase
from post_bot.application.use_cases.ensure_user import EnsureUserUseCase
from post_bot.application.use_cases.open_instructions import OpenInstructionsUseCase
from post_bot.application.use_cases.publish_approval_batch import PublishApprovalBatchUseCase
from post_bot.application.use_cases.publish_task import PublishTaskUseCase
from post_bot.application.use_cases.reserve_balance import ReserveBalanceUseCase
from post_bot.application.use_cases.start_upload_pipeline import StartUploadPipelineUseCase
from post_bot.application.use_cases.upload_intake import UploadIntakeUseCase
from post_bot.application.use_cases.validate_upload import ValidateUploadUseCase
from post_bot.bot.handlers.approval_action_command import ApprovalActionHandler
from post_bot.bot.handlers.approval_batch_command import BuildApprovalBatchHandler
from post_bot.bot.handlers.instructions_command import InstructionsCommandHandler
from post_bot.bot.handlers.language_selection import LanguageSelectionHandler
from post_bot.bot.handlers.telegram_upload_command import TelegramUploadCommandHandler
from post_bot.bot.handlers.upload_command import UploadCommandHandler
from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.infrastructure.db.mysql_uow import build_mysql_uow_from_dsn
from post_bot.infrastructure.excel.openpyxl_task_parser import OpenPyxlTaskParser
from post_bot.infrastructure.external import HttpPublisher
from post_bot.infrastructure.storage.local_file_storage import LocalFileStorage
from post_bot.infrastructure.storage.local_instruction_bundle_provider import LocalInstructionBundleProvider
from post_bot.infrastructure.storage.zip_builder import ZipBuilder
from post_bot.pipeline.modules.validation import ExcelContractValidator
from post_bot.shared.config import AppConfig
from post_bot.shared.enums import InterfaceLanguage
from post_bot.shared.errors import ExternalDependencyError


@dataclass(slots=True, frozen=True)
class BotWiring:
    uow: UnitOfWork
    file_storage: FileStoragePort
    language_selection: LanguageSelectionHandler
    instructions: InstructionsCommandHandler
    upload: TelegramUploadCommandHandler
    build_approval_batch: BuildApprovalBatchHandler
    approval_action: ApprovalActionHandler


def build_bot_wiring(
    *,
    uow: UnitOfWork,
    file_storage: FileStoragePort,
    excel_parser: ExcelTaskParserPort,
    instruction_bundle_provider: InstructionBundleProviderPort,
    logger: Logger,
    publisher: PublisherPort | None = None,
) -> BotWiring:
    ensure_user = EnsureUserUseCase(
        uow=uow,
        logger=logger.getChild("ensure_user"),
    )

    open_instructions = OpenInstructionsUseCase(
        uow=uow,
        bundle_provider=instruction_bundle_provider,
        logger=logger.getChild("open_instructions"),
    )

    start_upload_pipeline = StartUploadPipelineUseCase(
        intake=UploadIntakeUseCase(
            uow=uow,
            file_storage=file_storage,
            logger=logger.getChild("upload_intake"),
        ),
        validate=ValidateUploadUseCase(
            uow=uow,
            file_storage=file_storage,
            parser=excel_parser,
            validator=ExcelContractValidator(),
            logger=logger.getChild("validate_upload"),
        ),
        reserve=ReserveBalanceUseCase(
            uow=uow,
            logger=logger.getChild("reserve_balance"),
        ),
        create_tasks=TaskCreationUseCase(
            uow=uow,
            logger=logger.getChild("create_tasks"),
        ),
        logger=logger.getChild("start_upload_pipeline"),
    )

    effective_publisher = publisher or _NotConfiguredPublisher()
    publish_task_use_case = PublishTaskUseCase(
        uow=uow,
        publisher=effective_publisher,
        logger=logger.getChild("publish_task"),
    )
    publish_approval_batch = PublishApprovalBatchUseCase(
        uow=uow,
        publish_task_use_case=publish_task_use_case,
        logger=logger.getChild("publish_approval_batch"),
    )
    download_approval_batch = DownloadApprovalBatchUseCase(
        uow=uow,
        logger=logger.getChild("download_approval_batch"),
    )
    build_approval_batch = BuildApprovalBatchUseCase(
        uow=uow,
        file_storage=file_storage,
        artifact_storage=file_storage,
        zip_builder=ZipBuilder(),
        logger=logger.getChild("build_approval_batch"),
    )

    upload_handler = UploadCommandHandler(start_upload_pipeline=start_upload_pipeline)

    return BotWiring(
        uow=uow,
        file_storage=file_storage,
        language_selection=LanguageSelectionHandler(ensure_user=ensure_user),
        instructions=InstructionsCommandHandler(open_instructions=open_instructions),
        upload=TelegramUploadCommandHandler(
            ensure_user=ensure_user,
            upload_handler=upload_handler,
        ),
        build_approval_batch=BuildApprovalBatchHandler(build_approval_batch=build_approval_batch),
        approval_action=ApprovalActionHandler(
            publish_approval_batch=publish_approval_batch,
            download_approval_batch=download_approval_batch,
            file_storage=file_storage,
        ),
    )


def build_default_instruction_bundle_provider(*, project_root: str | Path) -> LocalInstructionBundleProvider:
    root = Path(project_root)
    template_path = root / "NEO_TEMPLATE.xlsx"
    readme_path = root / "README_PIPELINE.txt"
    return LocalInstructionBundleProvider(
        template_path=template_path,
        readme_paths_by_language={language: readme_path for language in InterfaceLanguage},
    )


def build_default_bot_wiring(
    *,
    config: AppConfig,
    project_root: str | Path,
    data_dir: str | Path | None = None,
    logger: Logger,
    instruction_bundle_provider: InstructionBundleProviderPort | None = None,
) -> BotWiring:
    root = Path(project_root)
    storage_root = Path(data_dir) if data_dir is not None else root / ".runtime_data"

    return build_bot_wiring(
        uow=build_mysql_uow_from_dsn(config.database_dsn),
        file_storage=LocalFileStorage(storage_root),
        excel_parser=OpenPyxlTaskParser(),
        instruction_bundle_provider=instruction_bundle_provider
        or build_default_instruction_bundle_provider(project_root=root),
        logger=logger,
        publisher=_build_bot_publisher(config),
    )


def _build_bot_publisher(config: AppConfig) -> PublisherPort:
    if not config.publisher_api_url:
        return _NotConfiguredPublisher()
    return HttpPublisher(
        endpoint_url=config.publisher_api_url,
        api_token=config.outbound_api_token,
        timeout_seconds=config.outbound_timeout_seconds,
    )


class _NotConfiguredPublisher:
    def publish(self, *, channel: str, html: str, scheduled_for):
        _ = (channel, html, scheduled_for)
        raise ExternalDependencyError(
            code="PUBLISHER_NOT_CONFIGURED",
            message="Publisher adapter is not configured for bot transport runtime.",
            retryable=False,
        )
