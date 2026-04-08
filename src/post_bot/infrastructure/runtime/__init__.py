"""Runtime services for background execution loops and bot handler composition."""

from post_bot.infrastructure.runtime.bot_wiring import (
    BotWiring,
    build_bot_wiring,
    build_default_bot_wiring,
    build_default_instruction_bundle_provider,
)
from post_bot.infrastructure.runtime.maintenance_runtime import (
    MaintenanceRuntime,
    MaintenanceRuntimeCommand,
    MaintenanceRuntimeResult,
)
from post_bot.infrastructure.runtime.telegram_runtime import (
    TelegramDownloadedFile,
    TelegramGatewayPort,
    TelegramPollingRuntime,
    TelegramRuntimeCommand,
    TelegramRuntimeResult,
)
from post_bot.infrastructure.runtime.worker_runtime import WorkerRuntime, WorkerRuntimeCommand, WorkerRuntimeResult
from post_bot.infrastructure.runtime.wiring import (
    RuntimeWiring,
    UnconfiguredLLMClient,
    UnconfiguredPublisher,
    UnconfiguredResearchClient,
    build_default_runtime_wiring,
    build_maintenance_runtime,
    build_worker_runtime,
)

__all__ = [
    "BotWiring",
    "MaintenanceRuntime",
    "MaintenanceRuntimeCommand",
    "MaintenanceRuntimeResult",
    "RuntimeWiring",
    "TelegramDownloadedFile",
    "TelegramGatewayPort",
    "TelegramPollingRuntime",
    "TelegramRuntimeCommand",
    "TelegramRuntimeResult",
    "UnconfiguredLLMClient",
    "UnconfiguredPublisher",
    "UnconfiguredResearchClient",
    "WorkerRuntime",
    "WorkerRuntimeCommand",
    "WorkerRuntimeResult",
    "build_bot_wiring",
    "build_default_bot_wiring",
    "build_default_instruction_bundle_provider",
    "build_default_runtime_wiring",
    "build_maintenance_runtime",
    "build_worker_runtime",
]
