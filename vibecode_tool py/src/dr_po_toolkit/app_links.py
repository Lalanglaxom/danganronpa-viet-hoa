from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

APP_LINK_SCHEME = "drpo"
APP_LINK_HOST = "open"
APP_LINK_SERVER_NAME = "DR_PO_Toolkit_EntryLinks_v1"


@dataclass(frozen=True, slots=True)
class AppEntryLink:
    file: Path
    context: str = ""
    line: int = 0


def build_entry_url(file: str | Path, *, context: str | None = None, line: int | None = None) -> str:
    path = Path(file).expanduser().resolve(strict=False)
    query: dict[str, str] = {"file": str(path)}
    if context:
        query["context"] = str(context)
    if line:
        query["line"] = str(max(0, int(line)))
    return f"{APP_LINK_SCHEME}://{APP_LINK_HOST}?{urlencode(query)}"


def parse_entry_url(value: str) -> AppEntryLink | None:
    try:
        parsed = urlparse(str(value).strip())
    except Exception:
        return None
    if parsed.scheme.casefold() != APP_LINK_SCHEME or parsed.netloc.casefold() != APP_LINK_HOST:
        return None
    params = parse_qs(parsed.query, keep_blank_values=True)
    raw_file = (params.get("file") or [""])[0].strip()
    if not raw_file:
        return None
    raw_line = (params.get("line") or ["0"])[0]
    try:
        line = max(0, int(raw_line or 0))
    except (TypeError, ValueError):
        line = 0
    context = (params.get("context") or [""])[0]
    return AppEntryLink(file=Path(raw_file).expanduser(), context=context, line=line)


def is_entry_url(value: str) -> bool:
    return parse_entry_url(value) is not None


def _launcher_parts() -> list[str]:
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve(strict=False))]
    project_root = Path(__file__).resolve().parents[2]
    launcher = project_root / "run_toolkit.py"
    return [str(Path(sys.executable).resolve(strict=False)), str(launcher)]


def protocol_command() -> str:
    return subprocess.list2cmdline(_launcher_parts()) + ' "%1"'


def register_url_protocol() -> bool:
    """Register drpo:// for the current user on Windows.

    Registration is best-effort and requires no administrator rights. Other
    platforms simply return False because their desktop registration differs.
    """
    if os.name != "nt":
        return False
    try:
        import winreg

        root = rf"Software\Classes\{APP_LINK_SCHEME}"
        command = protocol_command()
        icon = str(Path(sys.executable).resolve(strict=False))
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:DR PO Toolkit entry")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root + r"\DefaultIcon") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{icon}",0')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root + r"\shell\open\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
        return True
    except Exception:
        return False
