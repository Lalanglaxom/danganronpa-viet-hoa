import sys
from types import SimpleNamespace

from dr_po_toolkit.notifications import play_task_notification


def test_windows_task_notification_prefers_message_beep():
    message_calls = []
    sound_calls = []
    fake_winsound = SimpleNamespace(
        MB_ICONASTERISK=0x40,
        MB_ICONEXCLAMATION=0x30,
        MB_ICONHAND=0x10,
        SND_ALIAS=0x00010000,
        SND_ASYNC=0x0001,
        MessageBeep=lambda value: message_calls.append(value) or True,
        PlaySound=lambda alias, flags: sound_calls.append((alias, flags)),
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

    assert message_calls == [
        fake_winsound.MB_ICONASTERISK,
        fake_winsound.MB_ICONEXCLAMATION,
        fake_winsound.MB_ICONHAND,
    ]
    assert sound_calls == []


def test_windows_task_notification_falls_back_to_play_sound():
    calls = []
    fake_winsound = SimpleNamespace(
        MB_ICONASTERISK=0x40,
        SND_ALIAS=0x00010000,
        SND_ASYNC=0x0001,
        MessageBeep=lambda _value: False,
        PlaySound=lambda alias, flags: calls.append((alias, flags)),
    )
    sentinel = object()
    previous_module = sys.modules.get("winsound", sentinel)
    previous_platform = sys.platform
    try:
        sys.modules["winsound"] = fake_winsound
        sys.platform = "win32"
        play_task_notification("success")
    finally:
        sys.platform = previous_platform
        if previous_module is sentinel:
            sys.modules.pop("winsound", None)
        else:
            sys.modules["winsound"] = previous_module

    assert calls == [("SystemAsterisk", fake_winsound.SND_ALIAS | fake_winsound.SND_ASYNC)]


def test_windows_task_notification_uses_play_sound_when_message_beep_raises():
    calls = []

    def broken_message_beep(_value):
        raise RuntimeError("missing sound theme")

    fake_winsound = SimpleNamespace(
        MB_ICONASTERISK=0x40,
        SND_ALIAS=0x00010000,
        SND_ASYNC=0x0001,
        MessageBeep=broken_message_beep,
        PlaySound=lambda alias, flags: calls.append((alias, flags)),
    )
    sentinel = object()
    previous_module = sys.modules.get("winsound", sentinel)
    previous_platform = sys.platform
    try:
        sys.modules["winsound"] = fake_winsound
        sys.platform = "win32"
        play_task_notification("success")
    finally:
        sys.platform = previous_platform
        if previous_module is sentinel:
            sys.modules.pop("winsound", None)
        else:
            sys.modules["winsound"] = previous_module

    assert calls == [("SystemAsterisk", fake_winsound.SND_ALIAS | fake_winsound.SND_ASYNC)]


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
