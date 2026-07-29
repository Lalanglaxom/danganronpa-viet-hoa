from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .discovery import find_backup_for_file, find_segments, iter_po_files
from .models import POEntry
from .po_io import load_po, patch_msgstr_by_uid, save_po
from .text_utils import clt_tags, generic_tags, placeholders_by_type

SYSTEM_INSTRUCTIONS = """You are translating Danganronpa game PO entries into Vietnamese.
Return JSON only. Do not return PO text. Do not explain.
Rules:
- Translate only into Vietnamese.
- The English source_en/msgid is the source of truth. Translate by following the English meaning, wording, order, and intent as closely as natural Vietnamese allows.
- Use japanese_context only as secondary context for speaker tone, ambiguity, or terminology. Never let Japanese context override, expand, shorten, or change the English source.
- Preserve every CLT tag exactly, in the same order, e.g. <CLT 4> and <CLT>.
- Preserve placeholders and non-CLT tags exactly.
- Do not translate speaker names or ids.
- Do not leave translation empty.
- Keep tone natural for the speaker while still following the English closely.
- When previous_vietnamese_context is provided, treat it as mandatory continuity context for established Vietnamese wording, xưng hô/forms of address, speaker tone, and terminology. Reuse those established choices whenever they fit the current English meaning.
- Never translate, rewrite, quote, or return previous_vietnamese_context. It is context only; source_en for the current entry remains the source of truth.
"""


@dataclass(slots=True)
class TranslationJob:
    source_file: Path
    entries: list[POEntry]
    payload: dict[str, Any]


@dataclass(slots=True)
class TranslationError:
    uid: str
    msgctxt: str
    reason: str


def _clean_prompt(prompt: str | None = None) -> str:
    return (prompt or SYSTEM_INSTRUCTIONS).strip() + "\n"


def build_payload(
    entries: Iterable[POEntry],
    project: str = "Danganronpa",
    instructions: str | None = None,
    previous_vietnamese_context: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    items = []
    for entry in entries:
        items.append(
            {
                "uid": entry.uid,
                "msgctxt": entry.msgctxt,
                "speaker": entry.speaker,
                "japanese_context": entry.japanese_context,
                "source_en": entry.msgid,
            }
        )
    return {
        "task": "translate_po_entries_to_vietnamese",
        "project": project,
        "instructions": _clean_prompt(instructions),
        "previous_vietnamese_context": [dict(item) for item in (previous_vietnamese_context or [])],
        "entries": items,
        "required_response_schema": {
            "entries": [
                {
                    "uid": "same uid from request",
                    "translation": "Vietnamese msgstr only"
                }
            ]
        },
    }


def build_previous_vietnamese_context(
    file_entries: Iterable[POEntry],
    current_entry: POEntry,
    *,
    limit: int = 5,
    translation_overrides: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return translated context from the immediate previous entries in one PO file.

    The window is positional: only the five entries directly before the current
    entry are considered. Untranslated entries inside that window are omitted;
    the function never reaches farther back to replace them.
    """
    if limit <= 0:
        return []
    entries = list(file_entries)
    current_position = next((i for i, entry in enumerate(entries) if entry.uid == current_entry.uid), None)
    if current_position is None:
        return []
    overrides = translation_overrides or {}
    window = entries[max(0, current_position - limit):current_position]
    context: list[dict[str, Any]] = []
    for position, entry in enumerate(window, start=current_position - len(window)):
        translation = overrides.get(entry.uid, entry.msgstr)
        if not translation.strip():
            continue
        context.append(
            {
                "relative_position": position - current_position,
                "uid": entry.uid,
                "msgctxt": entry.msgctxt,
                "speaker": entry.speaker,
                "translation_vi": translation,
            }
        )
    return context


def build_prompt(payload: dict[str, Any], instructions: str | None = None) -> str:
    prompt = _clean_prompt(instructions or str(payload.get("instructions") or ""))
    request_payload = dict(payload)
    request_payload["instructions"] = prompt
    return (
        prompt
        + "\nReturn exactly this JSON shape. No Markdown fences. No explanation:\n"
        + json.dumps(request_payload["required_response_schema"], ensure_ascii=False, indent=2)
        + "\n\nREQUEST JSON:\n"
        + json.dumps(request_payload, ensure_ascii=False, indent=2)
    )


def untranslated_entries(path: str | Path, limit: int | None = None) -> list[POEntry]:
    po = load_po(path)
    entries = [e for e in po.entries if not e.msgstr.strip()]
    return entries[:limit] if limit else entries


def source_entries_for_translation(path: str | Path) -> list[POEntry]:
    """Return untranslated working entries with source/comments from Copy.po when available."""
    p = Path(path)
    work = load_po(p)
    missing = [e for e in work.entries if not e.msgstr.strip()]
    backup = find_backup_for_file(p)
    if not backup:
        return missing
    try:
        copy = load_po(backup)
    except Exception:
        return missing
    copy_by_ctx = {e.msgctxt: e for e in copy.entries}
    merged: list[POEntry] = []
    for work_entry in missing:
        src = copy_by_ctx.get(work_entry.msgctxt)
        if src is None:
            merged.append(work_entry)
            continue
        merged.append(
            POEntry(
                index=work_entry.index,
                msgctxt=work_entry.msgctxt,
                msgid=src.msgid,
                msgstr="",
                comments=list(src.comments),
                extracted_comments=list(src.extracted_comments),
                line=work_entry.line,
            )
        )
    return merged


def make_jobs_for_file(path: str | Path, batch_size: int = 20) -> list[TranslationJob]:
    p = Path(path)
    entries = source_entries_for_translation(p)
    jobs: list[TranslationJob] = []
    for i in range(0, len(entries), batch_size):
        batch = entries[i:i + batch_size]
        jobs.append(TranslationJob(source_file=p, entries=batch, payload=build_payload(batch)))
    return jobs


def write_manual_jobs(path: str | Path, out_dir: str | Path, batch_size: int = 20, max_files: int | None = None) -> list[Path]:
    """Create JSON + prompt files for manual Gemini use.

    The user can paste *_prompt.txt into Gemini, save the JSON response, then
    apply it with `apply-response`.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    processed = 0
    po_paths = list(iter_po_files(path)) if Path(path).is_dir() else [Path(path)]
    for po_path in po_paths:
        if max_files is not None and processed >= max_files:
            break
        jobs = make_jobs_for_file(po_path, batch_size=batch_size)
        if not jobs:
            continue
        processed += 1
        for job_idx, job in enumerate(jobs, start=1):
            safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", po_path.stem)
            base = out / f"{safe_name}_batch{job_idx:03d}"
            request_json = base.with_suffix(".request.json")
            prompt_txt = base.with_suffix(".prompt.txt")
            request_json.write_text(json.dumps(job.payload, ensure_ascii=False, indent=2), encoding="utf-8")
            prompt_txt.write_text(build_prompt(job.payload), encoding="utf-8")
            written.extend([request_json, prompt_txt])
    return written


def extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]
    return json.loads(text)


def parse_translation_response(text_or_json: str | Path | dict[str, Any]) -> dict[str, str]:
    if isinstance(text_or_json, dict):
        data = text_or_json
    else:
        p = Path(str(text_or_json))
        text = p.read_text(encoding="utf-8") if p.exists() else str(text_or_json)
        data = extract_json_object(text)
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise ValueError("translation response must contain entries: []")
    result: dict[str, str] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        uid = str(item.get("uid", "")).strip()
        translation = item.get("translation")
        if uid and isinstance(translation, str):
            result[uid] = unicodedata.normalize("NFC", translation)
    return result


def validate_translations(source_entries: Iterable[POEntry], translations: dict[str, str]) -> list[TranslationError]:
    source_by_uid = {e.uid: e for e in source_entries}
    errors: list[TranslationError] = []
    for uid, entry in source_by_uid.items():
        if uid not in translations:
            errors.append(TranslationError(uid, entry.msgctxt or "", "missing translation"))
            continue
        text = translations[uid]
        if not text.strip():
            errors.append(TranslationError(uid, entry.msgctxt or "", "empty translation"))
            continue
        if clt_tags(entry.msgid) != clt_tags(text):
            errors.append(TranslationError(uid, entry.msgctxt or "", f"CLT tags mismatch source={clt_tags(entry.msgid)!r} target={clt_tags(text)!r}"))
        if generic_tags(entry.msgid) != generic_tags(text):
            errors.append(TranslationError(uid, entry.msgctxt or "", "generic tags mismatch"))
        src_ph = placeholders_by_type(entry.msgid)
        tgt_ph = placeholders_by_type(text)
        for key in src_ph:
            if sorted(src_ph[key]) != sorted(tgt_ph[key]):
                errors.append(TranslationError(uid, entry.msgctxt or "", f"{key} placeholders mismatch"))
    for uid in translations:
        if uid not in source_by_uid:
            errors.append(TranslationError(uid, "", "unknown uid in response"))
    return errors


def apply_response_to_file(po_path: str | Path, response: str | Path | dict[str, Any], allow_partial: bool = False) -> tuple[int, list[TranslationError]]:
    po = load_po(po_path)
    candidates = [e for e in po.entries if not e.msgstr.strip()]
    translations = parse_translation_response(response)
    errors = validate_translations(candidates, translations)
    if errors and not allow_partial:
        return 0, errors
    bad_uids = {e.uid for e in errors}
    safe = {uid: val for uid, val in translations.items() if allow_partial or uid not in bad_uids}
    count = patch_msgstr_by_uid(po, safe)
    if count:
        save_po(po, po_path)
    return count, errors


class GeminiApiClient:
    """Optional Gemini API client.

    This module does not require google-genai unless you instantiate this class.
    Set GEMINI_API_KEY in your environment or pass api_key directly.
    The actual translation prompt is SYSTEM_INSTRUCTIONS by default; override it
    with the ``prompt`` argument or the GUI Gemini API Prompt box.
    """

    def __init__(self, api_key: str | None = None, model: str = "gemini-2.5-flash", prompt: str | None = None):
        import os
        try:
            from google import genai  # type: ignore
            from google.genai import types  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Install google-genai to use GeminiApiClient: pip install google-genai") from exc
        resolved_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not resolved_key:
            raise RuntimeError("Gemini API key missing. Enter it in the Gemini Web tab or set GEMINI_API_KEY.")
        self._client = genai.Client(api_key=resolved_key)
        self._types = types
        self.model = model
        self.prompt = _clean_prompt(prompt)

    def translate_payload(self, payload: dict[str, Any], prompt: str | None = None) -> dict[str, str]:
        active_prompt = _clean_prompt(prompt or self.prompt)
        response = self._client.models.generate_content(
            model=self.model,
            contents=build_prompt(payload, instructions=active_prompt),
            config=self._types.GenerateContentConfig(
                system_instruction=active_prompt,
                response_mime_type="application/json",
            ),
        )
        text = getattr(response, "text", "") or ""
        return parse_translation_response(text)


def translate_entries_with_client(
    entries: Iterable[POEntry],
    client: GeminiApiClient,
    batch_size: int = 20,
    sleep_seconds: float = 1.0,
    allow_partial: bool = False,
    prompt: str | None = None,
    progress: Callable[[int, int], None] | None = None,
    context_entries: Iterable[POEntry] | None = None,
    on_translation: Callable[[POEntry, str], None] | None = None,
) -> tuple[dict[str, str], list[TranslationError]]:
    """Translate entries with Gemini API.

    Interactive callers opt into strict one-entry requests by supplying
    ``context_entries``. Each request then receives up to the five immediately
    preceding translated entries from the same ordered PO file.

    Callers without ``context_entries`` retain the original batched behavior,
    controlled by ``batch_size``. This is used by Mass Translate File.
    """
    entry_list = list(entries)
    total_entries = len(entry_list)
    if progress is not None:
        progress(0, total_entries)
    all_errors: list[TranslationError] = []
    all_translations: dict[str, str] = {}

    if context_entries is not None:
        context_list = list(context_entries)
        for i, entry in enumerate(entry_list):
            previous_context = build_previous_vietnamese_context(
                context_list,
                entry,
                limit=5,
                translation_overrides=all_translations,
            )
            payload = build_payload(
                [entry],
                instructions=prompt or client.prompt,
                previous_vietnamese_context=previous_context,
            )
            translations = client.translate_payload(payload, prompt=prompt)
            errors = validate_translations([entry], translations)
            all_errors.extend(errors)
            bad = {error.uid for error in errors}
            translation = translations.get(entry.uid)
            if translation is not None and (allow_partial or entry.uid not in bad):
                all_translations[entry.uid] = translation
                if on_translation is not None:
                    on_translation(entry, translation)
            if progress is not None:
                progress(i + 1, total_entries)
            if sleep_seconds and i + 1 < total_entries:
                time.sleep(sleep_seconds)
        return all_translations, all_errors

    effective_batch_size = max(1, int(batch_size))
    for i in range(0, total_entries, effective_batch_size):
        batch = entry_list[i:i + effective_batch_size]
        payload = build_payload(batch, instructions=prompt or client.prompt)
        translations = client.translate_payload(payload, prompt=prompt)
        errors = validate_translations(batch, translations)
        all_errors.extend(errors)
        bad = {error.uid for error in errors}
        for entry in batch:
            translation = translations.get(entry.uid)
            if translation is None or (not allow_partial and entry.uid in bad):
                continue
            all_translations[entry.uid] = translation
            if on_translation is not None:
                on_translation(entry, translation)
        if progress is not None:
            progress(min(i + len(batch), total_entries), total_entries)
        if sleep_seconds and i + effective_batch_size < total_entries:
            time.sleep(sleep_seconds)
    return all_translations, all_errors


def translate_file_with_client(
    po_path: str | Path,
    client: GeminiApiClient,
    batch_size: int = 20,
    sleep_seconds: float = 1.0,
    allow_partial: bool = False,
    prompt: str | None = None,
) -> tuple[int, list[TranslationError]]:
    po = load_po(po_path)
    missing = source_entries_for_translation(po_path)
    translations, errors = translate_entries_with_client(
        missing,
        client,
        batch_size=batch_size,
        sleep_seconds=sleep_seconds,
        allow_partial=allow_partial,
        prompt=prompt,
    )
    changed = patch_msgstr_by_uid(po, translations)
    if changed:
        save_po(po, po_path)
    return changed, errors
