"""Simple transport-level health message builder."""

from __future__ import annotations

from post_bot.shared.enums import InterfaceLanguage
from post_bot.shared.localization import get_message

def build_health_message(interface_language: InterfaceLanguage) -> str:
    return get_message(interface_language, "SYSTEM_READY")