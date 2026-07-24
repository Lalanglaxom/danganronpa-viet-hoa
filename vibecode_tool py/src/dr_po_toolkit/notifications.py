from __future__ import annotations

import sys
from collections.abc import Callable


def play_task_notification(
    status: str = "success",
    *,
    fallback: Callable[[], object] | None = None,
) -> None:
    """Play a best-effort task notification without affecting task results."""
    normalized = str(status or "success").strip().lower()
    played = False

    if sys.platform.startswith("win"):
        try:
            import winsound

            message_type = {
                "failed": getattr(winsound, "MB_ICONHAND", 0x00000010),
                "stopped": getattr(winsound, "MB_ICONEXCLAMATION", 0x00000030),
                "cancelled": getattr(winsound, "MB_ICONEXCLAMATION", 0x00000030),
                "canceled": getattr(winsound, "MB_ICONEXCLAMATION", 0x00000030),
            }.get(normalized, getattr(winsound, "MB_ICONASTERISK", 0x00000040))

            message_beep = getattr(winsound, "MessageBeep", None)
            if callable(message_beep):
                try:
                    played = message_beep(message_type) is not False
                except Exception:
                    played = False

            if not played:
                alias = {
                    "failed": "SystemHand",
                    "stopped": "SystemExclamation",
                    "cancelled": "SystemExclamation",
                    "canceled": "SystemExclamation",
                }.get(normalized, "SystemAsterisk")
                try:
                    flags = getattr(winsound, "SND_ALIAS", 0) | getattr(winsound, "SND_ASYNC", 0)
                    winsound.PlaySound(alias, flags)
                    played = True
                except Exception:
                    played = False
        except Exception:
            played = False

    if not played and fallback is not None:
        try:
            fallback()
        except Exception:
            pass
