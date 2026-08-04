from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .dr_options import DR_FILE_OPTION_KEYS
from .linewrap import default_wrap_presets, normalize_wrap_presets
from .shortcuts import (
    FILE_SHORTCUT_ACTIONS,
    WRAP_SHORTCUT_ACTIONS,
    default_file_shortcuts,
    default_wrap_shortcuts,
    normalize_custom_shortcuts,
)

DEFAULT_CONFIG = {
    "last_path": "",
    "rules_file": "rules/mass_replace_rules.json",
    "soft_limit": 54,
    "hard_limit": 64,
    "max_cuts": 2,
    "linewrap_active_preset": 0,
    "linewrap_presets": default_wrap_presets(),
    "wrap_shortcuts": default_wrap_shortcuts(),
    "file_navigation_shortcuts": default_file_shortcuts(),
    "gemini_model": "gemini-2.5-flash",
    "translation_batch_size": 20,
    "gemini_web_cdp_url": "http://localhost:9222",
    "gemini_web_max_files": 59,
    "gemini_web_max_lines": 600,
    "gemini_web_max_entries": 40,
    "gemini_web_wait_seconds": 2.5,
    "gemini_web_timeout_seconds": 180,
    "gemini_web_retries": 2,
    "gemini_web_use_chatgpt": False,
    "gemini_translate_mode": "web",
    "gemini_api_key": "",
    # Interactive API buttons in Search, duplicate views, and PO Viewer.
    "gemini_api_single_model": "gemini-2.5-flash",
    "gemini_api_single_timeout_seconds": 90,
    "gemini_api_single_context_entries": 3,
    "gemini_api_single_context_across_files": False,
    "gemini_api_single_sleep_seconds": 0.0,
    "gemini_api_single_thinking_mode": "off",
    "gemini_api_single_max_output_tokens": 0,
    # Mass API runner in the AI Translation tab.
    "gemini_api_mass_model": "gemini-2.5-flash",
    "gemini_api_mass_timeout_seconds": 90,
    "gemini_api_mass_max_files": 59,
    "gemini_api_mass_batch_entries": 40,
    "gemini_api_mass_context_entries": 3,
    "gemini_api_mass_context_across_files": False,
    "gemini_api_mass_sleep_seconds": 1.0,
    "gemini_api_mass_thinking_mode": "off",
    "gemini_api_mass_max_output_tokens": 0,
    "po_viewer_suggest_min_score": 70,
    "po_viewer_clt_color_mode": False,
    "translafixer_hidden_duplicate_keys": [],
    "game_folder_path": "",
    "drat_folder_path": "",
    "danganviethoa_path": "",
    "extracted_path": "",
    "repack_path": "",
    "script_path": "",
    "wad_repack_path": "",
}
DEFAULT_CONFIG.update({f"working_{key}_path": "" for key in DR_FILE_OPTION_KEYS})
LEGACY_SYNC_DESTINATION_KEYS = {f"sync_{key}_path" for key in DR_FILE_OPTION_KEYS}
LEGACY_REPACK_CONFIG_KEYS = {"backup_sync_include_extra_path", "backup_sync_dr_options"}
LEGACY_GEMINI_API_KEYS = {
    "gemini_api_use_key",
    "gemini_api_model",
    "gemini_api_prompt",
    "gemini_api_sleep_seconds",
    "gemini_api_timeout_seconds",
    "gemini_api_context_entries",
    "gemini_api_context_across_files",
}
DEFAULT_CONFIG.update({
    f"{tab_key}_include_extra_path": False
    for tab_key in (
        "validate",
        "replace",
        "linewrap",
        "search",
        "translafixer",
        "po_viewer",
        "gemini_web",
    )
})


def default_config_path() -> Path:
    return Path.home() / ".dr_po_toolkit" / "config.json"


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else default_config_path()
    if not p.exists():
        merged = dict(DEFAULT_CONFIG)
        merged["linewrap_presets"] = default_wrap_presets()
        merged["wrap_shortcuts"] = default_wrap_shortcuts()
        merged["file_navigation_shortcuts"] = default_file_shortcuts()
        return merged
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        merged = dict(DEFAULT_CONFIG)
        merged["linewrap_presets"] = default_wrap_presets()
        merged["wrap_shortcuts"] = default_wrap_shortcuts()
        merged["file_navigation_shortcuts"] = default_file_shortcuts()
        return merged

    # Migrate the former single wrap setting into four editable presets.
    # Preset 1 starts with the known base-64 behavior but remains customizable.
    if "linewrap_presets" not in data:
        data["linewrap_presets"] = default_wrap_presets(
            data.get("soft_limit", DEFAULT_CONFIG["soft_limit"]),
            data.get("hard_limit", DEFAULT_CONFIG["hard_limit"]),
            data.get("max_cuts", DEFAULT_CONFIG["max_cuts"]),
        )
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)

    # Split the former shared Gemini API settings into independent interactive
    # and mass-translation profiles while preserving existing user choices.
    legacy_api_migrations = {
        "gemini_api_single_model": "gemini_api_model",
        "gemini_api_mass_model": "gemini_api_model",
        "gemini_api_single_timeout_seconds": "gemini_api_timeout_seconds",
        "gemini_api_mass_timeout_seconds": "gemini_api_timeout_seconds",
        "gemini_api_single_context_entries": "gemini_api_context_entries",
        "gemini_api_mass_context_entries": "gemini_api_context_entries",
        "gemini_api_single_context_across_files": "gemini_api_context_across_files",
        "gemini_api_mass_context_across_files": "gemini_api_context_across_files",
        "gemini_api_single_sleep_seconds": "gemini_api_sleep_seconds",
        "gemini_api_mass_sleep_seconds": "gemini_api_sleep_seconds",
        "gemini_api_mass_batch_entries": "gemini_web_max_entries",
        "gemini_api_mass_max_files": "gemini_web_max_files",
    }
    for new_key, old_key in legacy_api_migrations.items():
        if new_key not in data and old_key in data:
            merged[new_key] = data[old_key]
    if bool(data.get("gemini_api_use_key")):
        merged["gemini_translate_mode"] = "api"

    if "repack_dr_options" not in data and isinstance(data.get("backup_sync_dr_options"), list):
        merged["repack_dr_options"] = list(data["backup_sync_dr_options"])

    # Migrate the former separate DRAT path settings when possible. The new
    # DRAT Folder points at the manual-mode root containing EXTRACTED/REPACKED.
    if not str(merged.get("drat_folder_path", "")).strip():
        for legacy_key in ("extracted_path", "repack_path"):
            raw = str(merged.get(legacy_key, "")).strip()
            if not raw:
                continue
            candidate = Path(raw).expanduser()
            if candidate.name.casefold() in {"extracted", "repacked"}:
                merged["drat_folder_path"] = str(candidate.parent)
                break
    merged["linewrap_presets"] = normalize_wrap_presets(
        merged.get("linewrap_presets"),
        legacy_soft=merged.get("soft_limit", DEFAULT_CONFIG["soft_limit"]),
        legacy_hard=merged.get("hard_limit", DEFAULT_CONFIG["hard_limit"]),
        legacy_max_cuts=merged.get("max_cuts", DEFAULT_CONFIG["max_cuts"]),
    )
    normalized_shortcuts = normalize_custom_shortcuts(
        merged.get("wrap_shortcuts"),
        merged.get("file_navigation_shortcuts"),
    )
    merged["wrap_shortcuts"] = {action: normalized_shortcuts[action] for action in WRAP_SHORTCUT_ACTIONS}
    merged["file_navigation_shortcuts"] = {action: normalized_shortcuts[action] for action in FILE_SHORTCUT_ACTIONS}
    try:
        active_preset = int(merged.get("linewrap_active_preset", 0))
    except (TypeError, ValueError):
        active_preset = 0
    merged["linewrap_active_preset"] = max(0, min(3, active_preset))
    for key in LEGACY_SYNC_DESTINATION_KEYS | LEGACY_REPACK_CONFIG_KEYS | LEGACY_GEMINI_API_KEYS:
        merged.pop(key, None)
    return merged


def save_config(config: dict[str, Any], path: str | Path | None = None) -> None:
    p = Path(path) if path else default_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    legacy_keys = LEGACY_SYNC_DESTINATION_KEYS | LEGACY_REPACK_CONFIG_KEYS | LEGACY_GEMINI_API_KEYS
    payload = {key: value for key, value in config.items() if key not in legacy_keys}
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
