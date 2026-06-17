from __future__ import annotations

import time
from typing import Callable

StopFn = Callable[[], bool]


class OperationCancelled(Exception):
    """Raised when the user presses Stop Current Action in the GUI."""


def check_stop(stop_requested: StopFn | None = None) -> None:
    if stop_requested is not None and stop_requested():
        raise OperationCancelled("Stopped by user.")


def sleep_with_stop(seconds: float, stop_requested: StopFn | None = None, interval: float = 0.2) -> None:
    seconds = max(0.0, float(seconds))
    end = time.time() + seconds
    while time.time() < end:
        check_stop(stop_requested)
        time.sleep(min(interval, max(0.0, end - time.time())))
    check_stop(stop_requested)
