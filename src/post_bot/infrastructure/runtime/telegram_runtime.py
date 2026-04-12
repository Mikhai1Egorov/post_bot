"""Telegram long-polling runtime and update routing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import time
from logging import Logger
from typing import Any, Protocol

from post_bot.application.use_cases.get_available_posts import GetAvailablePostsCommand, GetAvailablePostsUseCase
from post_bot.application.use_cases.get_user_context import GetUserContextCommand, GetUserContextUseCase
from post_bot.application.use_cases.list_pending_approval_notifications import ListPendingApprovalNotificationsUseCase
from post_bot.application.use_cases.mark_approval_batch_notified import (
    MarkApprovalBatchNotifiedCommand,
    MarkApprovalBatchNotifiedUseCase,
)
from post_bot.bot.handlers.approval_action_command import HandleApprovalActionCommand
from post_bot.bot.handlers.approval_batch_command import HandleBuildApprovalBatchCommand
from post_bot.bot.handlers.instructions_command import HandleInstructionsCommand
from post_bot.bot.handlers.language_selection import HandleLanguageSelectionCommand
from post_bot.bot.handlers.telegram_upload_command import HandleTelegramUploadCommand
from post_bot.infrastructure.runtime.anti_spam import CallbackDebounceCache, FixedWindowRateLimiter
from post_bot.infrastructure.runtime.bot_wiring import BotWiring
from post_bot.shared.enums import InterfaceLanguage
from post_bot.shared.errors import AppError, BusinessRuleError, ValidationError
from post_bot.shared.localization import get_message, parse_interface_language
from post_bot.shared.logging import TimedLog, log_event


THROTTLE_RULES: dict[str, tuple[int, float, str | None]] = {
    "command_start": (5, 10.0, None),
    "command_language": (5, 10.0, None),
    "command_help": (5, 10.0, None),
    "callback_lang": (5, 10.0, None),
    "callback_instructions": (3, 10.0, "THROTTLED_RETRY_SHORT"),
    "callback_publish": (3, 10.0, "THROTTLED_RETRY_SHORT"),
    "callback_download": (3, 10.0, "THROTTLED_RETRY_SHORT"),
    "callback_upload_prompt": (3, 10.0, "THROTTLED_RETRY_SHORT"),
    "upload_document": (3, 20.0, "UPLOAD_TOO_FREQUENT"),
}
CALLBACK_DEBOUNCE_TTL_SECONDS = 2.0
DEFAULT_APPROVAL_DISPATCH_INTERVAL_SECONDS = 5.0
DEFAULT_APPROVAL_DISPATCH_BATCH_LIMIT = 20
DEFAULT_MAX_UPLOAD_SIZE_BYTES = 8 * 1024 * 1024
ALLOWED_UPLOAD_EXTENSIONS = {".xlsx"}
ALLOWED_UPLOAD_MIME_TYPES = {
    "application/octet-stream",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@dataclass(slots=True, frozen=True)
class TelegramDownloadedFile:
    file_name: str
    payload: bytes


class TelegramGatewayPort(Protocol):
    def get_updates(self, *, offset: int | None, timeout_seconds: int) -> list[dict[str, Any]]: ...

    def send_message(self, *, chat_id: int | str, text: str, reply_markup: dict[str, object] | None = None) -> None: ...

    def send_document(
        self,
        *,
        chat_id: int | str,
        file_name: str,
        payload: bytes,
        caption: str | None = None,
    ) -> None: ...

    def download_file(self, *, file_id: str, fallback_file_name: str | None = None) -> TelegramDownloadedFile: ...

    def answer_callback_query(self, *, callback_query_id: str) -> None: ...


class TelegramUpdateCheckpointPort(Protocol):
    def save(self, *, offset: int) -> None: ...


@dataclass(slots=True, frozen=True)
class TelegramRuntimeCommand:
    max_cycles: int | None = None
    max_failed_cycles: int | None = None
    poll_timeout_seconds: int = 30
    idle_sleep_seconds: float = 0.2
    offset: int | None = None
    approval_dispatch_interval_seconds: float = DEFAULT_APPROVAL_DISPATCH_INTERVAL_SECONDS
    approval_dispatch_batch_limit: int = DEFAULT_APPROVAL_DISPATCH_BATCH_LIMIT


@dataclass(slots=True, frozen=True)
class TelegramRuntimeResult:
    cycles_executed: int
    updates_processed: int
    updates_failed: int
    next_offset: int | None
    failed_cycles: int = 0
    terminated_early: bool = False


class TelegramPollingRuntime:
    """Runs Telegram long-polling loop and delegates business actions to handlers."""

    def __init__(
        self,
        *,
        gateway: TelegramGatewayPort,
        bot_wiring: BotWiring,
        get_available_posts: GetAvailablePostsUseCase,
        get_user_context: GetUserContextUseCase,
        list_pending_approval_notifications: ListPendingApprovalNotificationsUseCase,
        mark_approval_batch_notified: MarkApprovalBatchNotifiedUseCase,
        logger: Logger,
        update_checkpoint: TelegramUpdateCheckpointPort | None = None,
        now_provider: Callable[[], float] | None = None,
        rate_limiter: FixedWindowRateLimiter | None = None,
        callback_debounce_cache: CallbackDebounceCache | None = None,
        max_upload_size_bytes: int = DEFAULT_MAX_UPLOAD_SIZE_BYTES,
    ) -> None:
        self._gateway = gateway
        self._bot = bot_wiring
        self._get_available_posts = get_available_posts
        self._get_user_context = get_user_context
        self._list_pending_approval_notifications = list_pending_approval_notifications
        self._mark_approval_batch_notified = mark_approval_batch_notified
        self._logger = logger
        self._update_checkpoint = update_checkpoint
        self._now_provider = now_provider or time.monotonic
        self._rate_limiter = rate_limiter or FixedWindowRateLimiter(now_provider=self._now_provider)
        self._callback_debounce = callback_debounce_cache or CallbackDebounceCache(
            now_provider=self._now_provider,
            ttl_seconds=CALLBACK_DEBOUNCE_TTL_SECONDS,
        )
        self._max_upload_size_bytes = max_upload_size_bytes

    def run(self, command: TelegramRuntimeCommand) -> TelegramRuntimeResult:
        if command.max_cycles is not None and command.max_cycles < 1:
            raise BusinessRuleError(
                code="TELEGRAM_MAX_CYCLES_INVALID",
                message="max_cycles must be >= 1 when provided.",
                details={"max_cycles": command.max_cycles},
            )
        if command.max_failed_cycles is not None and command.max_failed_cycles < 1:
            raise BusinessRuleError(
                code="TELEGRAM_MAX_FAILED_CYCLES_INVALID",
                message="max_failed_cycles must be >= 1 when provided.",
                details={"max_failed_cycles": command.max_failed_cycles},
            )
        if command.poll_timeout_seconds < 1:
            raise BusinessRuleError(
                code="TELEGRAM_POLL_TIMEOUT_INVALID",
                message="poll_timeout_seconds must be >= 1.",
                details={"poll_timeout_seconds": command.poll_timeout_seconds},
            )
        if command.idle_sleep_seconds < 0:
            raise BusinessRuleError(
                code="TELEGRAM_IDLE_SLEEP_INVALID",
                message="idle_sleep_seconds must be >= 0.",
                details={"idle_sleep_seconds": command.idle_sleep_seconds},
            )
        if command.approval_dispatch_interval_seconds < 0:
            raise BusinessRuleError(
                code="TELEGRAM_APPROVAL_DISPATCH_INTERVAL_INVALID",
                message="approval_dispatch_interval_seconds must be >= 0.",
                details={"approval_dispatch_interval_seconds": command.approval_dispatch_interval_seconds},
            )
        if command.approval_dispatch_batch_limit < 1:
            raise BusinessRuleError(
                code="TELEGRAM_APPROVAL_DISPATCH_BATCH_LIMIT_INVALID",
                message="approval_dispatch_batch_limit must be >= 1.",
                details={"approval_dispatch_batch_limit": command.approval_dispatch_batch_limit},
            )

        offset = command.offset
        cycles_executed = 0
        updates_processed = 0
        updates_failed = 0
        failed_cycles = 0
        terminated_early = False
        last_approval_dispatch_at: float = -1_000_000_000.0

        while True:
            if command.max_cycles is not None and cycles_executed >= command.max_cycles:
                break

            cycles_executed += 1
            cycle_failed = False

            try:
                updates = self._gateway.get_updates(offset=offset, timeout_seconds=command.poll_timeout_seconds)
            except AppError as error:
                updates = []
                if error.code == "TELEGRAM_TIMEOUT":
                    # Long-poll timeout without updates is expected in idle periods.
                    log_event(
                        self._logger,
                        level=10,
                        module="infrastructure.telegram.runtime",
                        action="get_updates_timeout",
                        result="success",
                        error=error,
                    )
                else:
                    cycle_failed = True
                    log_event(
                        self._logger,
                        level=40,
                        module="infrastructure.telegram.runtime",
                        action="get_updates",
                        result="failure",
                        error=error,
                    )
            except Exception as exc:  # noqa: BLE001
                cycle_failed = True
                updates = []
                log_event(
                    self._logger,
                    level=40,
                    module="infrastructure.telegram.runtime",
                    action="get_updates",
                    result="failure",
                    error=AppError(
                        code="TELEGRAM_GET_UPDATES_UNHANDLED_ERROR",
                        message="Unhandled get_updates error.",
                        details={"exception": str(exc)},
                        retryable=False,
                    ),
                )

            for update in updates:
                update_id = self._read_update_id(update)
                if update_id is not None:
                    offset = update_id + 1
                    self._persist_update_offset(offset)

                try:
                    handled = self._handle_update(update)
                    if handled:
                        updates_processed += 1
                except AppError as error:
                    cycle_failed = True
                    updates_failed += 1
                    self._notify_update_error(update=update, error=error)
                    log_event(
                        self._logger,
                        level=40,
                        module="infrastructure.telegram.runtime",
                        action="update_handled",
                        result="failure",
                        error=error,
                        extra={"update_id": update_id},
                    )
                except Exception as exc:  # noqa: BLE001
                    cycle_failed = True
                    updates_failed += 1
                    wrapped_error = AppError(
                        code="TELEGRAM_RUNTIME_UNHANDLED_ERROR",
                        message="Unhandled runtime error.",
                        details={"exception": str(exc), "update_id": update_id},
                        retryable=False,
                    )
                    self._notify_update_error(update=update, error=wrapped_error)
                    log_event(
                        self._logger,
                        level=40,
                        module="infrastructure.telegram.runtime",
                        action="update_handled",
                        result="failure",
                        error=wrapped_error,
                    )

            now_value = self._now_provider()
            if now_value - last_approval_dispatch_at >= command.approval_dispatch_interval_seconds:
                try:
                    self._dispatch_pending_approval_notifications(limit=command.approval_dispatch_batch_limit)
                except AppError as error:
                    cycle_failed = True
                    log_event(
                        self._logger,
                        level=40,
                        module="infrastructure.telegram.runtime",
                        action="approval_notifications_dispatch",
                        result="failure",
                        error=error,
                    )
                except Exception as exc:  # noqa: BLE001
                    cycle_failed = True
                    log_event(
                        self._logger,
                        level=40,
                        module="infrastructure.telegram.runtime",
                        action="approval_notifications_dispatch",
                        result="failure",
                        error=AppError(
                            code="TELEGRAM_APPROVAL_DISPATCH_UNHANDLED_ERROR",
                            message="Unhandled approval dispatch error.",
                            details={"exception": str(exc)},
                            retryable=False,
                        ),
                    )
                finally:
                    last_approval_dispatch_at = now_value

            if cycle_failed:
                failed_cycles += 1
                if command.max_failed_cycles is not None and failed_cycles >= command.max_failed_cycles:
                    terminated_early = True
                    break

            if not updates:
                time.sleep(command.idle_sleep_seconds)

        return TelegramRuntimeResult(
            cycles_executed=cycles_executed,
            updates_processed=updates_processed,
            updates_failed=updates_failed,
            next_offset=offset,
            failed_cycles=failed_cycles,
            terminated_early=terminated_early,
        )

    def _handle_update(self, update: dict[str, Any]) -> bool:
        timer = TimedLog()
        callback_query = update.get("callback_query")
        if isinstance(callback_query, dict):
            handled = self._handle_callback_query(callback_query)
            if handled:
                log_event(
                    self._logger,
                    level=20,
                    module="infrastructure.telegram.runtime",
                    action="callback_handled",
                    result="success",
                    duration_ms=timer.elapsed_ms(),
                )
            return handled

        message = update.get("message")
        if not isinstance(message, dict):
            return False

        text = message.get("text")
        if isinstance(text, str):
            handled = self._handle_text_message(message, text)
            if handled:
                log_event(
                    self._logger,
                    level=20,
                    module="infrastructure.telegram.runtime",
                    action="text_message_handled",
                    result="success",
                    duration_ms=timer.elapsed_ms(),
                )
            return handled

        document = message.get("document")
        if isinstance(document, dict):
            handled = self._handle_document_message(message, document)
            if handled:
                log_event(
                    self._logger,
                    level=20,
                    module="infrastructure.telegram.runtime",
                    action="document_message_handled",
                    result="success",
                    duration_ms=timer.elapsed_ms(),
                )
            return handled

        return False

    @staticmethod
    def _read_update_id(update: dict[str, Any]) -> int | None:
        value = update.get("update_id")
        if isinstance(value, int):
            return value
        return None

    def _handle_text_message(self, message: dict[str, Any], text: str) -> bool:
        command = text.strip().lower()
        if command not in {"/start", "/language", "/help"}:
            return False

        chat_id = self._message_chat_id(message)
        telegram_user_id = self._message_user_id(message)
        if chat_id is None or telegram_user_id is None:
            return False

        action_name = f"command_{command.removeprefix('/')}"
        if not self._is_action_allowed(
            telegram_user_id=telegram_user_id,
            action_name=action_name,
            chat_id=chat_id,
            language=InterfaceLanguage.EN,
        ):
            return True

        self._send_language_prompt(chat_id)
        return True

    def _handle_document_message(self, message: dict[str, Any], document: dict[str, Any]) -> bool:
        chat_id = self._message_chat_id(message)
        telegram_user_id = self._message_user_id(message)
        if chat_id is None or telegram_user_id is None:
            return False

        context = self._get_user_context.execute(GetUserContextCommand(telegram_user_id=telegram_user_id))
        if not context.found or context.user_id is None or context.interface_language is None:
            self._send_language_prompt(chat_id)
            return True

        if not self._is_action_allowed(
            telegram_user_id=telegram_user_id,
            action_name="upload_document",
            chat_id=chat_id,
            language=context.interface_language,
        ):
            return True

        metadata_error_message = self._validate_upload_document_metadata(
            document=document,
            language=context.interface_language,
        )
        if metadata_error_message is not None:
            self._gateway.send_message(
                chat_id=chat_id,
                text=metadata_error_message,
                reply_markup=self._action_keyboard(context.interface_language),
            )
            return True

        file_id = document.get("file_id")
        if not isinstance(file_id, str) or not file_id:
            raise ValidationError(
                code="TELEGRAM_DOCUMENT_ID_MISSING",
                message="Telegram document file_id is missing.",
            )

        fallback_name = document.get("file_name") if isinstance(document.get("file_name"), str) else None
        downloaded = self._gateway.download_file(file_id=file_id, fallback_file_name=fallback_name)

        result = self._bot.upload.handle(
            HandleTelegramUploadCommand(
                telegram_user_id=telegram_user_id,
                original_filename=downloaded.file_name,
                payload=downloaded.payload,
                interface_language=context.interface_language,
            )
        )

        self._gateway.send_message(
            chat_id=chat_id,
            text=result.response_text,
            reply_markup=self._action_keyboard(context.interface_language),
        )
        return True

    def _handle_callback_query(self, callback_query: dict[str, Any]) -> bool:
        callback_id = callback_query.get("id")
        if isinstance(callback_id, str) and callback_id:
            try:
                self._gateway.answer_callback_query(callback_query_id=callback_id)
            except AppError as error:
                if self._is_callback_answer_expired_error(error):
                    log_event(
                        self._logger,
                        level=30,
                        module="infrastructure.telegram.runtime",
                        action="callback_answer_expired",
                        result="failure",
                        error=error,
                    )
                else:
                    raise

        data = callback_query.get("data")
        if not isinstance(data, str):
            return False

        chat_id = self._callback_chat_id(callback_query)
        telegram_user_id = self._callback_user_id(callback_query)
        if chat_id is None or telegram_user_id is None:
            return False

        callback_message_id = self._callback_message_id(callback_query)
        if self._is_callback_rapid_duplicate(
            telegram_user_id=telegram_user_id,
            callback_data=data,
            callback_message_id=callback_message_id,
        ):
            return True

        if data.startswith("lang:"):
            language_code = data.split(":", 1)[1].strip()
            if language_code == "header":
                return True
            if not self._is_action_allowed(
                telegram_user_id=telegram_user_id,
                action_name="callback_lang",
                chat_id=chat_id,
                language=InterfaceLanguage.EN,
            ):
                return True
            language = parse_interface_language(language_code)
            result = self._bot.language_selection.handle(
                HandleLanguageSelectionCommand(
                    telegram_user_id=telegram_user_id,
                    interface_language=language,
                )
            )
            self._gateway.send_message(
                chat_id=chat_id,
                text=result.response_text,
                reply_markup=self._action_keyboard(result.interface_language),
            )
            return True

        context = self._get_user_context.execute(GetUserContextCommand(telegram_user_id=telegram_user_id))
        if not context.found or context.user_id is None or context.interface_language is None:
            self._send_language_prompt(chat_id)
            return True

        if data == "instructions":
            if not self._is_action_allowed(
                telegram_user_id=telegram_user_id,
                action_name="callback_instructions",
                chat_id=chat_id,
                language=context.interface_language,
            ):
                return True
            result = self._bot.instructions.handle(
                HandleInstructionsCommand(
                    user_id=context.user_id,
                    interface_language=context.interface_language,
                )
            )
            self._gateway.send_document(
                chat_id=chat_id,
                file_name=result.template_file_name,
                payload=result.template_bytes,
            )
            self._gateway.send_document(
                chat_id=chat_id,
                file_name=result.readme_file_name,
                payload=result.readme_bytes,
            )
            self._gateway.send_message(
                chat_id=chat_id,
                text=result.response_text,
                reply_markup=self._action_keyboard(context.interface_language),
            )
            return True

        if data == "upload":
            if not self._is_action_allowed(
                telegram_user_id=telegram_user_id,
                action_name="callback_upload_prompt",
                chat_id=chat_id,
                language=context.interface_language,
            ):
                return True
            self._gateway.send_message(
                chat_id=chat_id,
                text=get_message(context.interface_language, "UPLOAD_PROMPT"),
                reply_markup=self._action_keyboard(context.interface_language),
            )
            return True

        if data.startswith("approval_publish:"):
            if not self._is_action_allowed(
                telegram_user_id=telegram_user_id,
                action_name="callback_publish",
                chat_id=chat_id,
                language=context.interface_language,
            ):
                return True
            batch_id = self._parse_batch_id(data, prefix="approval_publish:")
            result = self._bot.approval_action.handle(
                HandleApprovalActionCommand(
                    user_id=context.user_id,
                    batch_id=batch_id,
                    action="publish",
                    interface_language=context.interface_language,
                )
            )
            self._gateway.send_message(
                chat_id=chat_id,
                text=result.response_text,
                reply_markup=self._action_keyboard(context.interface_language),
            )
            return True

        if data.startswith("approval_download:"):
            if not self._is_action_allowed(
                telegram_user_id=telegram_user_id,
                action_name="callback_download",
                chat_id=chat_id,
                language=context.interface_language,
            ):
                return True
            batch_id = self._parse_batch_id(data, prefix="approval_download:")
            result = self._bot.approval_action.handle(
                HandleApprovalActionCommand(
                    user_id=context.user_id,
                    batch_id=batch_id,
                    action="download",
                    interface_language=context.interface_language,
                )
            )
            if result.success and result.zip_file_name and result.zip_payload is not None:
                self._gateway.send_document(
                    chat_id=chat_id,
                    file_name=result.zip_file_name,
                    payload=result.zip_payload,
                )
            self._gateway.send_message(
                chat_id=chat_id,
                text=result.response_text,
                reply_markup=self._action_keyboard(context.interface_language),
            )
            return True

        return False

    def _dispatch_pending_approval_notifications(self, *, limit: int) -> None:
        pending = self._list_pending_approval_notifications.execute(limit=limit)
        dispatched_count = 0

        for notification in pending.notifications:
            available_posts = self._get_available_posts.execute(
                GetAvailablePostsCommand(user_id=notification.user_id)
            ).available_posts_count
            for upload_id in notification.upload_ids:
                if dispatched_count >= limit:
                    return

                build_result = self._bot.build_approval_batch.handle(HandleBuildApprovalBatchCommand(upload_id=upload_id))
                if not build_result.success or build_result.batch_id is None:
                    continue

                self._gateway.send_message(
                    chat_id=notification.telegram_user_id,
                    text=self._build_approval_ready_text(
                        language=notification.interface_language,
                        available_posts_count=available_posts,
                    ),
                    reply_markup=self._approval_keyboard(
                        language=notification.interface_language,
                        batch_id=build_result.batch_id,
                    ),
                )

                notified_result = self._mark_approval_batch_notified.execute(
                    MarkApprovalBatchNotifiedCommand(batch_id=build_result.batch_id)
                )
                if not notified_result.success:
                    log_event(
                        self._logger,
                        level=30,
                        module="infrastructure.telegram.runtime",
                        action="approval_batch_mark_notified",
                        result="failure",
                        extra={
                            "batch_id": build_result.batch_id,
                            "error_code": notified_result.error_code,
                        },
                    )
                dispatched_count += 1

    @staticmethod
    def _parse_batch_id(data: str, *, prefix: str) -> int:
        batch_raw = data[len(prefix) :].strip()
        try:
            batch_id = int(batch_raw)
        except ValueError as exc:
            raise ValidationError(
                code="TELEGRAM_APPROVAL_BATCH_ID_INVALID",
                message="Approval batch id is invalid.",
                details={"data": data},
            ) from exc
        if batch_id <= 0:
            raise ValidationError(
                code="TELEGRAM_APPROVAL_BATCH_ID_INVALID",
                message="Approval batch id must be positive.",
                details={"data": data},
            )
        return batch_id

    def _send_language_prompt(self, chat_id: int) -> None:
        self._gateway.send_message(
            chat_id=chat_id,
            text="\u2063",
            reply_markup=self._language_keyboard(),
        )

    @staticmethod
    def _language_keyboard() -> dict[str, object]:
        header_text = get_message(InterfaceLanguage.EN, "SELECT_INTERFACE_LANGUAGE")
        return {
            "inline_keyboard": [
                [
                    {"text": header_text, "callback_data": "lang:header"},
                ],
                [
                    {"text": "\U0001F1EC\U0001F1E7 English", "callback_data": "lang:en"},
                    {"text": "\U0001F1F7\U0001F1FA Russian", "callback_data": "lang:ru"},
                    {"text": "\U0001F1FA\U0001F1E6 Ukrainian", "callback_data": "lang:uk"},
                ],
                [
                    {"text": "\U0001F1EA\U0001F1F8 Spanish", "callback_data": "lang:es"},
                    {"text": "\U0001F1E8\U0001F1F3 Chinese", "callback_data": "lang:zh"},
                ],
                [
                    {"text": "\U0001F1EE\U0001F1F3 Hindi", "callback_data": "lang:hi"},
                    {"text": "\U0001F1F8\U0001F1E6 Arabic", "callback_data": "lang:ar"},
                ],
            ]
        }

    @staticmethod
    def _action_keyboard(language: InterfaceLanguage) -> dict[str, object]:
        return {
            "inline_keyboard": [
                [
                    {
                        "text": f"\U0001F4D8 {get_message(language, 'BUTTON_HOW_TO_USE')}",
                        "callback_data": "instructions",
                    }
                ],
            ]
        }

    @staticmethod
    def _approval_keyboard(*, language: InterfaceLanguage, batch_id: int) -> dict[str, object]:
        return {
            "inline_keyboard": [
                [
                    {
                        "text": get_message(language, "BUTTON_PUBLISH"),
                        "callback_data": f"approval_publish:{batch_id}",
                    },
                    {
                        "text": get_message(language, "BUTTON_DOWNLOAD_ARCHIVE"),
                        "callback_data": f"approval_download:{batch_id}",
                    },
                ],
            ]
        }
    @staticmethod
    def _build_approval_ready_text(*, language: InterfaceLanguage, available_posts_count: int) -> str:
        base_text = get_message(language, "APPROVAL_READY")
        available_text = get_message(language, "AVAILABLE_POSTS", available=available_posts_count)
        return f"\u2705 {base_text}\n\n{available_text}"

    def _persist_update_offset(self, offset: int) -> None:
        if self._update_checkpoint is None:
            return
        try:
            self._update_checkpoint.save(offset=offset)
        except Exception as exc:  # noqa: BLE001
            log_event(
                self._logger,
                level=30,
                module="infrastructure.telegram.runtime",
                action="update_checkpoint_save_failed",
                result="failure",
                extra={"offset": offset, "error": str(exc)},
            )

    def _is_action_allowed(
        self,
        *,
        telegram_user_id: int,
        action_name: str,
        chat_id: int,
        language: InterfaceLanguage,
    ) -> bool:
        rule = THROTTLE_RULES.get(action_name)
        if rule is None:
            return True

        limit, window_seconds, message_key = rule
        allowed = self._rate_limiter.allow(
            key=(telegram_user_id, action_name),
            limit=limit,
            window_seconds=window_seconds,
        )
        if allowed:
            return True

        log_event(
            self._logger,
            level=20,
            module="infrastructure.telegram.runtime",
            action="throttle_rejected",
            result="success",
            extra={
                "telegram_user_id": telegram_user_id,
                "action_name": action_name,
                "limit": limit,
                "window_seconds": window_seconds,
            },
        )
        if message_key is not None:
            self._gateway.send_message(chat_id=chat_id, text=get_message(language, message_key))
        return False

    def _is_callback_rapid_duplicate(
        self,
        *,
        telegram_user_id: int,
        callback_data: str,
        callback_message_id: int | None,
    ) -> bool:
        if not self._should_debounce_callback(callback_data):
            return False

        duplicate = self._callback_debounce.is_duplicate(
            key=(telegram_user_id, callback_data, callback_message_id),
        )
        if duplicate:
            log_event(
                self._logger,
                level=20,
                module="infrastructure.telegram.runtime",
                action="callback_debounce_hit",
                result="success",
                extra={
                    "telegram_user_id": telegram_user_id,
                    "callback_data": callback_data,
                    "message_id": callback_message_id,
                },
            )
        return duplicate

    @staticmethod
    def _should_debounce_callback(callback_data: str) -> bool:
        return (
            callback_data.startswith("lang:")
            or callback_data == "instructions"
            or callback_data.startswith("approval_publish:")
            or callback_data.startswith("approval_download:")
        )

    def _validate_upload_document_metadata(
        self,
        *,
        document: dict[str, Any],
        language: InterfaceLanguage,
    ) -> str | None:
        file_name = document.get("file_name")
        if not isinstance(file_name, str) or "." not in file_name:
            return get_message(language, "UPLOAD_FILE_TYPE_UNSUPPORTED")

        extension = file_name.rsplit(".", 1)[-1].lower()
        if f".{extension}" not in ALLOWED_UPLOAD_EXTENSIONS:
            return get_message(language, "UPLOAD_FILE_TYPE_UNSUPPORTED")

        mime_type = document.get("mime_type")
        if isinstance(mime_type, str) and mime_type and mime_type.lower() not in ALLOWED_UPLOAD_MIME_TYPES:
            return get_message(language, "UPLOAD_FILE_TYPE_UNSUPPORTED")

        file_size = document.get("file_size")
        if isinstance(file_size, int) and file_size > self._max_upload_size_bytes:
            max_size_mb = max(1, self._max_upload_size_bytes // (1024 * 1024))
            return get_message(language, "UPLOAD_FILE_TOO_LARGE", max_size_mb=max_size_mb)
        return None

    @staticmethod
    def _message_chat_id(message: dict[str, Any]) -> int | None:
        chat = message.get("chat")
        if not isinstance(chat, dict):
            return None
        chat_id = chat.get("id")
        if not isinstance(chat_id, int):
            return None
        return chat_id

    @staticmethod
    def _message_user_id(message: dict[str, Any]) -> int | None:
        user = message.get("from")
        if not isinstance(user, dict):
            return None
        user_id = user.get("id")
        if not isinstance(user_id, int):
            return None
        return user_id

    @staticmethod
    def _callback_chat_id(callback_query: dict[str, Any]) -> int | None:
        message = callback_query.get("message")
        if not isinstance(message, dict):
            return None
        return TelegramPollingRuntime._message_chat_id(message)

    @staticmethod
    def _callback_user_id(callback_query: dict[str, Any]) -> int | None:
        user = callback_query.get("from")
        if not isinstance(user, dict):
            return None
        user_id = user.get("id")
        if not isinstance(user_id, int):
            return None
        return user_id

    @staticmethod
    def _callback_message_id(callback_query: dict[str, Any]) -> int | None:
        message = callback_query.get("message")
        if not isinstance(message, dict):
            return None
        message_id = message.get("message_id")
        if not isinstance(message_id, int):
            return None
        return message_id

    def _notify_update_error(self, *, update: dict[str, Any], error: AppError) -> None:
        chat_id = self._update_chat_id(update)
        if chat_id is None:
            return

        language = self._resolve_update_language(update)
        text = self._build_update_error_text(language=language, error=error)
        reply_markup = self._action_keyboard(language)
        try:
            self._gateway.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
            )
        except Exception as notification_exc:  # noqa: BLE001
            log_event(
                self._logger,
                level=30,
                module="infrastructure.telegram.runtime",
                action="update_error_notification_failed",
                result="failure",
                extra={
                    "chat_id": chat_id,
                    "error_code": error.code,
                    "notification_error": str(notification_exc),
                },
            )

    def _resolve_update_language(self, update: dict[str, Any]) -> InterfaceLanguage:
        telegram_user_id = self._update_user_id(update)
        if telegram_user_id is None:
            return InterfaceLanguage.EN

        try:
            context = self._get_user_context.execute(GetUserContextCommand(telegram_user_id=telegram_user_id))
        except Exception:  # noqa: BLE001
            return InterfaceLanguage.EN

        if not context.found or context.interface_language is None:
            return InterfaceLanguage.EN
        return context.interface_language

    @staticmethod
    def _is_callback_answer_expired_error(error: AppError) -> bool:
        if error.code != "TELEGRAM_HTTP_ERROR":
            return False

        details = error.details if isinstance(error.details, dict) else {}
        status_raw = details.get("status")
        try:
            status = int(status_raw) if status_raw is not None else 0
        except (TypeError, ValueError):
            status = 0

        if status != 400:
            return False

        body_text = str(details.get("body") or "").casefold()
        reason_text = str(details.get("reason") or "").casefold()
        return (
            "query is too old" in body_text
            or "query id is invalid" in body_text
            or "query is too old" in reason_text
            or "query id is invalid" in reason_text
        )

    @staticmethod
    def _build_update_error_text(*, language: InterfaceLanguage, error: AppError) -> str:
        if error.code == "PUBLISH_BOT_NOT_IN_CHANNEL":
            return get_message(language, "PUBLISH_BOT_NOT_IN_CHANNEL")
        if isinstance(error, ValidationError) and error.code.startswith("EXCEL_"):
            return "\n".join(
                (
                    f"{get_message(language, 'VALIDATION_FAILED')} ({error.code})",
                    get_message(language, "VALIDATION_REUPLOAD_HINT"),
                )
            )
        return get_message(language, "APPROVAL_ACTION_FAILED", error_code=error.code)

    @staticmethod
    def _update_chat_id(update: dict[str, Any]) -> int | None:
        message = update.get("message")
        if isinstance(message, dict):
            return TelegramPollingRuntime._message_chat_id(message)

        callback_query = update.get("callback_query")
        if isinstance(callback_query, dict):
            return TelegramPollingRuntime._callback_chat_id(callback_query)
        return None

    @staticmethod
    def _update_user_id(update: dict[str, Any]) -> int | None:
        message = update.get("message")
        if isinstance(message, dict):
            return TelegramPollingRuntime._message_user_id(message)

        callback_query = update.get("callback_query")
        if isinstance(callback_query, dict):
            return TelegramPollingRuntime._callback_user_id(callback_query)
        return None
