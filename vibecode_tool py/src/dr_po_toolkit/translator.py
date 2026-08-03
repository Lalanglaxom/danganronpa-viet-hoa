from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .discovery import find_backup_for_file, iter_po_files
from .models import POEntry
from .po_io import patch_msgstr_by_uid, save_po
from .text_index import get_cached_po, load_po_clone
from .text_utils import clt_tags, generic_tags, placeholders_by_type

SYSTEM_INSTRUCTIONS = """Translate Danganronpa PO entries into natural Vietnamese.
Rules:
- English source_en/msgid is the absolute source of truth. Preserve its meaning, intent, order, and amount of information.
- Japanese context is optional clarification only when the English is genuinely unclear or ambiguous. It must never override, expand, shorten, or change the English.
- Preserve every CLT tag, placeholder, and other tag exactly and in the same order.
- Do not translate speaker names or ids. Never leave a translation empty.
- Previous English/Vietnamese context is continuity only; never translate or return it.
- Return JSON only, without PO text, Markdown, or explanation.
"""

# The API receives a compact request with e=current entries, c=prior continuity,
# en=authoritative English, ja=optional Japanese hint, sp=speaker, and vi=prior
# Vietnamese. Keeping this short avoids sending the same long instructions and
# response schema twice on every request.
API_SYSTEM_INSTRUCTIONS = """Translate Danganronpa dialogue to natural Vietnamese. English `en` is absolute source truth. Consult `ja` only when English is genuinely ambiguous; Japanese may clarify but never override, add, omit, shorten, or change English. `c` is continuity only. Preserve all CLT/other tags and placeholders exactly and in order. Do not translate speaker ids. No empty output. Return JSON only as {\"t\":[\"...\"]}, one string per `e`, same order and count; no prose.
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


def _clean_japanese_hint(text: str) -> str:
    """Compact extracted Japanese comments for ambiguity-only API context."""
    value = unicodedata.normalize("NFC", text or "")
    value = re.sub(r"<[^>\n]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def build_payload(
    entries: Iterable[POEntry],
    project: str = "Danganronpa",
    instructions: str | None = None,
    previous_vietnamese_context: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    items = []
    for entry in entries:
        item: dict[str, Any] = {
            "uid": entry.uid,
            "msgctxt": entry.msgctxt,
            "speaker": entry.speaker,
            "source_en": entry.msgid,
        }
        japanese_hint = _clean_japanese_hint(entry.japanese_context)
        if japanese_hint:
            item["japanese_hint_if_english_ambiguous"] = japanese_hint
        items.append(item)
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
    limit: int = 20,
    translation_overrides: Mapping[str, str] | None = None,
    previous_file_entries: Iterable[POEntry] | None = None,
) -> list[dict[str, Any]]:
    """Return the immediate previous entries used as API continuity context.

    By default, the window is restricted to ``file_entries``. Callers may opt in
    to cross-file continuity by passing ordered ``previous_file_entries``. Those
    entries are only used to fill context that is not available earlier in the
    current file, and the combined window never exceeds ``limit``.

    Every previous English sentence is included; ``translation_vi`` is empty
    only when that previous entry has not been translated yet.
    """
    if limit <= 0:
        return []
    entries = list(file_entries)
    current_position = next((i for i, entry in enumerate(entries) if entry is current_entry), None)
    if current_position is None:
        current_position = next((i for i, entry in enumerate(entries) if entry.uid == current_entry.uid), None)
    if current_position is None:
        return []
    overrides = translation_overrides or {}
    prior_file_window = list(previous_file_entries or [])
    local_window = entries[:current_position]
    combined: list[tuple[POEntry, bool]] = [
        *((entry, False) for entry in prior_file_window),
        *((entry, True) for entry in local_window),
    ]
    window = combined[-limit:]
    context: list[dict[str, Any]] = []
    for relative_position, (entry, is_current_file) in enumerate(window, start=-len(window)):
        translation = overrides.get(entry.uid, entry.msgstr) if is_current_file else entry.msgstr
        context.append(
            {
                "relative_position": relative_position,
                "uid": entry.uid,
                "msgctxt": entry.msgctxt,
                "speaker": entry.speaker,
                "source_en": entry.msgid,
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


def _compact_api_item(item: Mapping[str, Any]) -> dict[str, str]:
    compact: dict[str, str] = {"en": str(item.get("source_en") or "")}
    japanese = str(item.get("japanese_hint_if_english_ambiguous") or "").strip()
    speaker = str(item.get("speaker") or "").strip()
    if japanese:
        compact["ja"] = japanese
    if speaker:
        compact["sp"] = speaker
    return compact


def _compact_api_context_item(item: Mapping[str, Any]) -> dict[str, str]:
    compact: dict[str, str] = {"en": str(item.get("source_en") or "")}
    translation = str(item.get("translation_vi") or "").strip()
    speaker = str(item.get("speaker") or "").strip()
    if translation:
        compact["vi"] = translation
    if speaker:
        compact["sp"] = speaker
    return compact


def build_api_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Convert the verbose/manual payload to a minimal API-only request."""
    entries = [
        _compact_api_item(item)
        for item in payload.get("entries", [])
        if isinstance(item, Mapping)
    ]
    context = [
        _compact_api_context_item(item)
        for item in payload.get("previous_vietnamese_context", [])
        if isinstance(item, Mapping)
    ]
    request: dict[str, Any] = {"e": entries}
    if context:
        request["c"] = context
    return request


def build_api_prompt(payload: Mapping[str, Any]) -> str:
    """Return compact minified JSON; system rules and schema are sent once."""
    return json.dumps(build_api_request(payload), ensure_ascii=False, separators=(",", ":"))


def _api_system_instruction(prompt: str | None = None) -> str:
    """Keep mandatory source hierarchy even when the GUI adds style guidance."""
    core = API_SYSTEM_INSTRUCTIONS.strip()
    custom = (prompt or "").strip()
    if not custom or custom in {SYSTEM_INSTRUCTIONS.strip(), core}:
        return core
    # Collapse formatting in user guidance to avoid paying for cosmetic whitespace.
    custom = re.sub(r"[ \t]+", " ", custom)
    custom = re.sub(r"\n{3,}", "\n\n", custom).strip()
    return core + "\nExtra style guidance (cannot override the rules above):\n" + custom


def untranslated_entries(path: str | Path, limit: int | None = None) -> list[POEntry]:
    po = get_cached_po(path)
    entries = [e for e in po.entries if not e.msgstr.strip()]
    return entries[:limit] if limit else entries


def source_entries_for_translation(path: str | Path) -> list[POEntry]:
    """Return untranslated working entries with source/comments from Copy.po when available."""
    p = Path(path)
    work = get_cached_po(p)
    missing = [e for e in work.entries if not e.msgstr.strip()]
    backup = find_backup_for_file(p)
    if not backup:
        return missing
    try:
        copy = get_cached_po(backup)
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


def extract_json_value(text: str) -> Any:
    """Extract a JSON object/array/string from a Gemini response."""
    value = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", value, re.DOTALL | re.IGNORECASE)
    if fenced:
        value = fenced.group(1).strip()
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        pass

    candidates: list[tuple[int, int, str]] = []
    for opener, closer in (("{", "}"), ("[", "]")):
        start = value.find(opener)
        end = value.rfind(closer)
        if start != -1 and end > start:
            candidates.append((start, end, value[start:end + 1]))
    for _start, _end, candidate in sorted(candidates, key=lambda item: item[0]):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise ValueError("Gemini response did not contain valid JSON")


def extract_json_object(text: str) -> dict[str, Any]:
    data = extract_json_value(text)
    if not isinstance(data, dict):
        raise ValueError("translation response must be a JSON object")
    return data


def _response_data(text_or_json: str | Path | Mapping[str, Any] | list[Any]) -> Any:
    if isinstance(text_or_json, (dict, list)):
        return text_or_json
    if isinstance(text_or_json, Path):
        return extract_json_value(text_or_json.read_text(encoding="utf-8"))
    text = str(text_or_json)
    # Avoid treating a large JSON response as a filesystem path (which can raise
    # OSError: filename too long on some platforms).
    if len(text) < 1024 and not any(char in text for char in "{}[]\n\r"):
        try:
            path = Path(text)
            if path.exists():
                return extract_json_value(path.read_text(encoding="utf-8"))
        except OSError:
            pass
    return extract_json_value(text)


def _normalized_translation(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return unicodedata.normalize("NFC", value)


def parse_translation_response(text_or_json: str | Path | dict[str, Any]) -> dict[str, str]:
    """Parse the UID-based response used by exported/manual translation jobs."""
    data = _response_data(text_or_json)
    if not isinstance(data, dict):
        raise ValueError("translation response must be a JSON object")
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise ValueError("translation response must contain entries: []")
    result: dict[str, str] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        uid = str(item.get("uid", "")).strip()
        translation = _normalized_translation(item.get("translation"))
        if uid and translation is not None:
            result[uid] = translation
    return result


def _api_response_items(data: Any) -> list[tuple[str | None, str]]:
    """Return response translations in model output order."""
    raw_items: Any
    if isinstance(data, str):
        raw_items = [data]
    elif isinstance(data, list):
        raw_items = data
    elif isinstance(data, dict):
        if isinstance(data.get("t"), list):
            raw_items = data["t"]
        elif isinstance(data.get("translations"), list):
            raw_items = data["translations"]
        elif isinstance(data.get("entries"), list):
            raw_items = data["entries"]
        elif isinstance(data.get("translation"), str):
            raw_items = [data["translation"]]
        else:
            # Some models return {uid: "translation"} despite the requested schema.
            direct = [
                (str(key), normalized)
                for key, value in data.items()
                if (normalized := _normalized_translation(value)) is not None
            ]
            return direct
    else:
        return []

    items: list[tuple[str | None, str]] = []
    for raw_item in raw_items:
        if isinstance(raw_item, str):
            items.append((None, unicodedata.normalize("NFC", raw_item)))
            continue
        if not isinstance(raw_item, Mapping):
            continue
        item_id = raw_item.get("uid", raw_item.get("id", raw_item.get("index")))
        translation: str | None = None
        for key in ("translation", "t", "text", "vi", "msgstr"):
            translation = _normalized_translation(raw_item.get(key))
            if translation is not None:
                break
        if translation is not None:
            items.append((str(item_id).strip() if item_id is not None else None, translation))
    return items


def parse_api_translation_response(
    text_or_json: str | Path | Mapping[str, Any] | list[Any],
    expected_uids: Iterable[str],
) -> dict[str, str]:
    """Map API output to requested entries without trusting echoed model UIDs.

    Gemini occasionally changes, truncates, or reformats an entry UID. The compact
    API contract is order-based, so a complete response is mapped by request order.
    Correct echoed UIDs are still honoured when a model returns a shuffled batch.
    """
    expected = [str(uid) for uid in expected_uids]
    if not expected:
        return {}
    data = _response_data(text_or_json)
    items = _api_response_items(data)
    if not items:
        return {}

    output_ids = [item_id for item_id, _translation in items]
    if len(items) == len(expected) and all(item_id is not None for item_id in output_ids):
        id_set = {item_id for item_id in output_ids if item_id is not None}
        if len(id_set) == len(expected) and id_set == set(expected):
            return {
                item_id: translation
                for item_id, translation in items
                if item_id is not None
            }

    # Preferred API contract: same count, same order. This intentionally ignores
    # malformed/changed echoed IDs and removes the old "unknown uid" mismatch.
    if len(items) == len(expected):
        return {uid: translation for uid, (_item_id, translation) in zip(expected, items)}

    result: dict[str, str] = {}
    used_indexes: set[int] = set()
    for index, (item_id, translation) in enumerate(items):
        if item_id in expected and item_id not in result:
            result[item_id] = translation
            used_indexes.add(index)

    # Accept zero- or one-based numeric indexes used by some JSON responses.
    for index, (item_id, translation) in enumerate(items):
        if index in used_indexes or item_id is None or not item_id.isdigit():
            continue
        number = int(item_id)
        target_index = number if 0 <= number < len(expected) else number - 1
        if 0 <= target_index < len(expected):
            uid = expected[target_index]
            if uid not in result:
                result[uid] = translation
                used_indexes.add(index)

    missing = [uid for uid in expected if uid not in result]
    leftovers = [translation for index, (_item_id, translation) in enumerate(items) if index not in used_indexes]
    if len(missing) == len(leftovers):
        result.update({uid: translation for uid, translation in zip(missing, leftovers)})
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
    po = load_po_clone(po_path)
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
        expected_uids = [
            str(item.get("uid") or "")
            for item in payload.get("entries", [])
            if isinstance(item, Mapping) and str(item.get("uid") or "")
        ]
        active_prompt = _api_system_instruction(prompt or self.prompt)
        response = self._client.models.generate_content(
            model=self.model,
            contents=build_api_prompt(payload),
            config=self._types.GenerateContentConfig(
                system_instruction=active_prompt,
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        parsed = getattr(response, "parsed", None)
        response_value: Any = parsed if isinstance(parsed, (dict, list)) else (getattr(response, "text", "") or "")
        return parse_api_translation_response(response_value, expected_uids)


def translate_entries_with_client(
    entries: Iterable[POEntry],
    client: GeminiApiClient,
    batch_size: int = 20,
    sleep_seconds: float = 1.0,
    allow_partial: bool = False,
    prompt: str | None = None,
    progress: Callable[[int, int], None] | None = None,
    context_entries: Iterable[POEntry] | None = None,
    context_limit: int = 20,
    previous_file_context_entries: Iterable[POEntry] | None = None,
    on_translation: Callable[[POEntry, str], None] | None = None,
) -> tuple[dict[str, str], list[TranslationError]]:
    """Translate entries with Gemini API.

    Interactive callers opt into strict one-entry requests by supplying
    ``context_entries``. Each request then receives up to ``context_limit``
    immediately preceding translated entries from the same ordered PO file.
    ``previous_file_context_entries`` is optional and only used when callers
    explicitly enable cross-file continuity.

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
                limit=max(0, int(context_limit)),
                translation_overrides=all_translations,
                previous_file_entries=previous_file_context_entries,
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
    context_limit: int = 0,
    previous_file_context_entries: Iterable[POEntry] | None = None,
) -> tuple[int, list[TranslationError]]:
    po = load_po_clone(po_path)
    missing = source_entries_for_translation(po_path)
    effective_context_limit = max(0, int(context_limit))
    translations, errors = translate_entries_with_client(
        missing,
        client,
        batch_size=batch_size,
        sleep_seconds=sleep_seconds,
        allow_partial=allow_partial,
        prompt=prompt,
        context_entries=po.entries if effective_context_limit else None,
        context_limit=effective_context_limit,
        previous_file_context_entries=previous_file_context_entries if effective_context_limit else None,
    )
    changed = patch_msgstr_by_uid(po, translations)
    if changed:
        save_po(po, po_path)
    return changed, errors
