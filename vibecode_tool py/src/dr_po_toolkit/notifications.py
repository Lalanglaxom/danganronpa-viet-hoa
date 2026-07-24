from __future__ import annotations

import sys
from collections.abc import Callable


def play_task_notification(
    status: str = "success",
    *,
    fallback: Callable[[], object] | None = None,
) -> None:
    """Play a non-blocking native sound after a task finishes."""
    normalized = str(status or "success").strip().lower()
    try:
        if sys.platform.startswith("win"):
            import winsound

            alias = {
                "failed": "SystemHand",
                "stopped": "SystemExclamation",
                "cancelled": "SystemExclamation",
                "canceled": "SystemExclamation",
            }.get(normalized, "SystemAsterisk")
            winsound.PlaySound(alias, winsound.SND_ALIAS | winsound.SND_ASYNC)
            return
    except Exception:
        # Notification failures must never affect the completed task.
        pass

    if fallback is not None:
        try:
            fallback()
        except Exception:
            pass
