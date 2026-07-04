from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = {
    "last_path": "",
    "rules_file": "rules/mass_replace_rules.json",
    "soft_limit": 58,
    "hard_limit": 64,
    "max_cuts": 2,
    "gemini_model": "gemini-2.5-flash",
    "translation_batch_size": 20,
    "gemini_web_cdp_url": "http://localhost:9222",
    "gemini_web_max_files": 59,
    "gemini_web_max_lines": 600,
    "gemini_web_max_entries": 40,
    "gemini_web_wait_seconds": 8.0,
    "gemini_web_timeout_seconds": 180,
    "gemini_web_retries": 2,
    "gemini_translate_mode": "web",
    "gemini_api_use_key": False,
    "gemini_api_key": "",
    "gemini_api_model": "gemini-2.5-flash",
    "gemini_api_prompt": "",
    "gemini_api_sleep_seconds": 1.0,
    "po_viewer_suggest_min_score": 70,
    "po_viewer_clt_color_mode": False,
}



def default_config_path() -> Path:
    return Path.home() / ".dr_po_toolkit" / "config.json"


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else default_config_path()
    if not p.exists():
        return dict(DEFAULT_CONFIG)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return dict(DEFAULT_CONFIG)
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)
    return merged


def save_config(config: dict[str, Any], path: str | Path | None = None) -> None:
    p = Path(path) if path else default_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
