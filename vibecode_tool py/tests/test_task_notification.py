import sys
from types import SimpleNamespace

from dr_po_toolkit.notifications import play_task_notification


def test_windows_task_notification_uses_async_native_sounds():
    calls = []
    fake_winsound = SimpleNamespace(
        SND_ALIAS=0x00010000,
        SND_ASYNC=0x0001,
        PlaySound=lambda alias, flags: calls.append((alias, flags)),
    )
    sentinel = object()
    previous_module = sys.modules.get("winsound", sentinel)
    previous_platform = sys.platform
    try:
        sys.modules["winsound"] = fake_winsound
        sys.platform = "win32"
        play_task_notification("success")
        play_task_notification("stopped")
        play_task_notification("failed")
    finally:
        sys.platform = previous_platform
        if previous_module is sentinel:
            sys.modules.pop("winsound", None)
        else:
            sys.modules["winsound"] = previous_module

    expected_flags = fake_winsound.SND_ALIAS | fake_winsound.SND_ASYNC
    assert calls == [
        ("SystemAsterisk", expected_flags),
        ("SystemExclamation", expected_flags),
        ("SystemHand", expected_flags),
    ]


def test_task_notification_falls_back_without_raising():
    calls = []
    previous_platform = sys.platform
    try:
        sys.platform = "linux"
        play_task_notification("success", fallback=lambda: calls.append("beep"))
        play_task_notification("success", fallback=lambda: (_ for _ in ()).throw(RuntimeError("no audio")))
    finally:
        sys.platform = previous_platform

    assert calls == ["beep"]
