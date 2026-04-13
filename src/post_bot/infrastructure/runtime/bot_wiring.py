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
from post_bot.application.use_cases.get_available_posts import GetAvailablePostsUseCase
from post_bot.application.use_cases.open_instructions import OpenInstructionsUseCase
from post_bot.application.use_cases.publish_approval_batch import PublishApprovalBatchUseCase
from post_bot.application.use_cases.publish_task import PublishTaskUseCase
from post_bot.application.use_cases.release_upload_reservation import ReleaseUploadReservationUseCase
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
from post_bot.infrastructure.db.mysql_uow import build_mysql_uow
from post_bot.infrastructure.excel.openpyxl_task_parser import OpenPyxlTaskParser
from post_bot.infrastructure.external import LocalArtifactPublisher, TelegramBotPublisher
from post_bot.infrastructure.storage.local_file_storage import LocalFileStorage
from post_bot.infrastructure.storage.local_instruction_bundle_provider import LocalInstructionBundleProvider
from post_bot.infrastructure.storage.zip_builder import ZipBuilder
from post_bot.pipeline.modules.validation import ExcelContractValidator
from post_bot.shared.config import AppConfig
from post_bot.shared.enums import InterfaceLanguage


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

    get_available_posts = GetAvailablePostsUseCase(
        uow=uow,
        logger=logger.getChild("get_available_posts"),
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
        release_reservation=ReleaseUploadReservationUseCase(
            uow=uow,
            logger=logger.getChild("release_upload_reservation"),
        ),
        logger=logger.getChild("start_upload_pipeline"),
        get_available_posts=get_available_posts,
    )

    effective_publisher = publisher or LocalArtifactPublisher()
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
        language_selection=LanguageSelectionHandler(
            ensure_user=ensure_user,
            get_available_posts=get_available_posts,
        ),
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



def _build_default_publisher(config: AppConfig) -> PublisherPort:
    if config.telegram_bot_token:
        return TelegramBotPublisher(
            bot_token=config.telegram_bot_token,
            timeout_seconds=config.outbound_timeout_seconds,
        )
    return LocalArtifactPublisher()


def _instruction_template_candidates(*, project_root: Path) -> tuple[Path, ...]:
    return (
        project_root / "docs" / "NEO_TEMPLATE.xlsx",
        project_root / "NEO_TEMPLATE.xlsx",
    )


def _readme_suffixes(language: InterfaceLanguage) -> tuple[str, ...]:
    if language == InterfaceLanguage.EN:
        # Support both ENG and EN naming, prefer ENG to match project files.
        return ("ENG", "EN")
    return (language.value.upper(),)


def _default_readme_candidates(*, project_root: Path, language: InterfaceLanguage) -> tuple[Path, ...]:
    docs_root = project_root / "docs"
    docs_readme_dir = docs_root / "readme"
    readme_dir = project_root / "readme"

    candidates: list[Path] = []
    for suffix in _readme_suffixes(language):
        file_name = f"README_PIPELINE_{suffix}.txt"
        candidates.append(docs_readme_dir / file_name)
        candidates.append(docs_root / file_name)
        candidates.append(readme_dir / file_name)

    candidates.append(docs_root / "README_PIPELINE.txt")
    candidates.append(project_root / "README_PIPELINE.txt")
    return tuple(candidates)


def _build_default_readme_paths_by_language(*, project_root: Path) -> dict[InterfaceLanguage, Path]:
    mapping: dict[InterfaceLanguage, Path] = {}
    for language in InterfaceLanguage:
        candidates = _default_readme_candidates(project_root=project_root, language=language)
        selected = next((path for path in candidates if path.exists()), candidates[0])
        mapping[language] = selected
    return mapping


def build_default_instruction_bundle_provider(*, project_root: str | Path) -> LocalInstructionBundleProvider:
    root = Path(project_root)
    template_candidates = _instruction_template_candidates(project_root=root)
    template_path = next((path for path in template_candidates if path.exists()), template_candidates[0])

    return LocalInstructionBundleProvider(
        template_path=template_path,
        readme_paths_by_language=_build_default_readme_paths_by_language(project_root=root),
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
        uow=build_mysql_uow(
            host=config.db_host,
            port=config.db_port,
            user=config.db_user,
            password=config.db_password,
            database=config.db_name,
        ),
        file_storage=LocalFileStorage(storage_root),
        excel_parser=OpenPyxlTaskParser(),
        instruction_bundle_provider=instruction_bundle_provider
        or build_default_instruction_bundle_provider(project_root=root),
        logger=logger,
        publisher=_build_default_publisher(config),
    )
