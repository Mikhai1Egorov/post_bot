"""Telegram long-polling runtime and update routing."""

from __future__ import annotations

from dataclasses import dataclass
import time
from logging import Logger
from typing import Any, Protocol

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
from post_bot.infrastructure.runtime.bot_wiring import BotWiring
from post_bot.shared.enums import InterfaceLanguage
from post_bot.shared.errors import AppError, BusinessRuleError, ValidationError
from post_bot.shared.localization import get_message, parse_interface_language
from post_bot.shared.logging import TimedLog, log_event


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


@dataclass(slots=True, frozen=True)
class TelegramRuntimeCommand:
    max_cycles: int | None = None
    max_failed_cycles: int | None = None
    poll_timeout_seconds: int = 30
    idle_sleep_seconds: float = 0.2
    offset: int | None = None


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
        get_user_context: GetUserContextUseCase,
        list_pending_approval_notifications: ListPendingApprovalNotificationsUseCase,
        mark_approval_batch_notified: MarkApprovalBatchNotifiedUseCase,
        logger: Logger,
    ) -> None:
        self._gateway = gateway
        self._bot = bot_wiring
        self._get_user_context = get_user_context
        self._list_pending_approval_notifications = list_pending_approval_notifications
        self._mark_approval_batch_notified = mark_approval_batch_notified
        self._logger = logger

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

        offset = command.offset
        cycles_executed = 0
        updates_processed = 0
        updates_failed = 0
        failed_cycles = 0
        terminated_early = False

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

            try:
                self._dispatch_pending_approval_notifications()
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
        if chat_id is None:
            return False

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

        if data.startswith("lang:"):
            language_code = data.split(":", 1)[1].strip()
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
            self._gateway.send_message(
                chat_id=chat_id,
                text=get_message(context.interface_language, "UPLOAD_PROMPT"),
                reply_markup=self._action_keyboard(context.interface_language),
            )
            return True

        if data.startswith("approval_publish:"):
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

    def _dispatch_pending_approval_notifications(self) -> None:
        pending = self._list_pending_approval_notifications.execute()
        for notification in pending.notifications:
            for upload_id in notification.upload_ids:
                build_result = self._bot.build_approval_batch.handle(HandleBuildApprovalBatchCommand(upload_id=upload_id))
                if not build_result.success or build_result.batch_id is None:
                    continue

                self._gateway.send_message(
                    chat_id=notification.telegram_user_id,
                    text=get_message(notification.interface_language, "APPROVAL_READY"),
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
            text=get_message(InterfaceLanguage.EN, "SELECT_INTERFACE_LANGUAGE"),
            reply_markup=self._language_keyboard(),
        )

    @staticmethod
    def _language_keyboard() -> dict[str, object]:
        return {
            "inline_keyboard": [
                [
                    {"text": "\U0001F1EC\U0001F1E7 English", "callback_data": "lang:en"},
                    {"text": "\U0001F1F7\U0001F1FA Russian", "callback_data": "lang:ru"},
                    {"text": "\U0001F1FA\U0001F1E6 Ukrainian", "callback_data": "lang:uk"},
                ],
                [
                    {"text": "\U0001F1EA\U0001F1F8 Spanish", "callback_data": "lang:es"},
                    {"text": "\U0001F1E8\U0001F1F3 Chinese", "callback_data": "lang:zh"},
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
                [
                    {
                        "text": f"\U0001F4CA {get_message(language, 'BUTTON_UPLOAD_TASKS')}",
                        "callback_data": "upload",
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







