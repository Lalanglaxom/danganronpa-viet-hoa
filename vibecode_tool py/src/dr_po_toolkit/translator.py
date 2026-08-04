from __future__ import annotations

import json
import re
import threading
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


@dataclass(slots=True)
class GeminiApiUsage:
    """Accumulated token metadata returned by Gemini API requests."""

    requests: int = 0
    prompt_tokens: int = 0
    candidate_tokens: int = 0
    thought_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0

    def add(self, other: "GeminiApiUsage") -> None:
        self.requests += other.requests
        self.prompt_tokens += other.prompt_tokens
        self.candidate_tokens += other.candidate_tokens
        self.thought_tokens += other.thought_tokens
        self.cached_tokens += other.cached_tokens
        self.total_tokens += other.total_tokens

    def as_dict(self) -> dict[str, int]:
        return {
            "requests": self.requests,
            "prompt_tokens": self.prompt_tokens,
            "candidate_tokens": self.candidate_tokens,
            "thought_tokens": self.thought_tokens,
            "cached_tokens": self.cached_tokens,
            "total_tokens": self.total_tokens,
        }

    def summary(self) -> str:
        return " | ".join(
            [
                f"requests={self.requests}",
                f"input={self.prompt_tokens}",
                f"output={self.candidate_tokens}",
                f"thinking={self.thought_tokens}",
                f"cached={self.cached_tokens}",
                f"total={self.total_tokens}",
            ]
        )


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


def _api_response_schema(entry_count: int) -> dict[str, Any]:
    """Minimal structured-output schema for order-based translation results."""
    count = max(1, int(entry_count))
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "t": {
                "type": "array",
                "description": "Vietnamese translations for each request entry, in exactly the same order.",
                "items": {"type": "string"},
                "minItems": count,
                "maxItems": count,
            }
        },
        "required": ["t"],
    }


def _auto_max_output_tokens(payload: Mapping[str, Any]) -> int:
    """Return a generous safety cap without paying for unused capacity."""
    entries = [item for item in payload.get("entries", []) if isinstance(item, Mapping)]
    source_chars = sum(len(str(item.get("source_en") or "")) for item in entries)
    # Vietnamese can tokenize less compactly than English. This cap is purposely
    # generous; it only prevents runaway output and does not reserve/bill tokens.
    return min(65536, max(256, source_chars * 2 + max(1, len(entries)) * 96))


def _usage_int(metadata: Any, snake_name: str, camel_name: str) -> int:
    value: Any = None
    if isinstance(metadata, Mapping):
        value = metadata.get(snake_name, metadata.get(camel_name))
    else:
        value = getattr(metadata, snake_name, None)
        if value is None:
            value = getattr(metadata, camel_name, None)
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def gemini_usage_from_response(response: Any) -> GeminiApiUsage:
    metadata = getattr(response, "usage_metadata", None)
    if metadata is None:
        metadata = getattr(response, "usageMetadata", None)
    if metadata is None:
        return GeminiApiUsage(requests=1)
    usage = GeminiApiUsage(
        requests=1,
        prompt_tokens=_usage_int(metadata, "prompt_token_count", "promptTokenCount"),
        candidate_tokens=_usage_int(metadata, "candidates_token_count", "candidatesTokenCount"),
        thought_tokens=_usage_int(metadata, "thoughts_token_count", "thoughtsTokenCount"),
        cached_tokens=_usage_int(metadata, "cached_content_token_count", "cachedContentTokenCount"),
        total_tokens=_usage_int(metadata, "total_token_count", "totalTokenCount"),
    )
    if not usage.total_tokens:
        usage.total_tokens = usage.prompt_tokens + usage.candidate_tokens + usage.thought_tokens
    return usage


def _enum_text(value: Any) -> str:
    if value is None:
        return ""
    raw = getattr(value, "value", value)
    return str(raw).strip()


def _gemini_response_diagnostic(
    response: Any,
    *,
    returned: int | None = None,
    mapped: int | None = None,
    expected: int | None = None,
) -> str:
    """Return a compact, non-content diagnostic for incomplete API output."""
    parts: list[str] = []
    prompt_feedback = getattr(response, "prompt_feedback", None)
    if prompt_feedback is None:
        prompt_feedback = getattr(response, "promptFeedback", None)
    block_reason = _enum_text(getattr(prompt_feedback, "block_reason", None))
    if not block_reason:
        block_reason = _enum_text(getattr(prompt_feedback, "blockReason", None))
    if block_reason:
        parts.append(f"block_reason={block_reason}")

    candidates = getattr(response, "candidates", None)
    first_candidate = candidates[0] if isinstance(candidates, (list, tuple)) and candidates else None
    if first_candidate is not None:
        finish_reason = _enum_text(getattr(first_candidate, "finish_reason", None))
        if not finish_reason:
            finish_reason = _enum_text(getattr(first_candidate, "finishReason", None))
        if finish_reason:
            parts.append(f"finish_reason={finish_reason}")
        finish_message = str(
            getattr(first_candidate, "finish_message", None)
            or getattr(first_candidate, "finishMessage", None)
            or ""
        ).strip()
        if finish_message:
            finish_message = re.sub(r"\s+", " ", finish_message)
            parts.append(f"finish_message={finish_message[:180]}")

    if returned is not None:
        parts.append(f"items={returned}")
    if mapped is not None and expected is not None:
        parts.append(f"mapped={mapped}/{expected}")
    return "; ".join(parts)


def _thinking_config(types: Any, model: str, mode: str) -> Any | None:
    """Build model-family-correct thinking controls.

    Gemini 2.5 uses token budgets. Gemini 3 uses named levels and cannot be
    guaranteed fully off. Flash/Flash-Lite models use ``minimal`` for the
    optimized "off" setting; Pro models fall back to their lowest supported
    ``low`` level because they do not accept ``minimal``.
    Unknown model families keep their server default for compatibility.
    """
    normalized_model = (model or "").strip().casefold()
    normalized_mode = (mode or "off").strip().casefold()
    if normalized_mode not in {"off", "minimal", "low", "medium", "high", "dynamic"}:
        normalized_mode = "off"
    is_gemini_3 = bool(re.search(r"(?:^|-)gemini-3(?:[.-]|$)", normalized_model))
    is_gemini_25 = "gemini-2.5" in normalized_model
    if not is_gemini_3 and not is_gemini_25:
        return None
    try:
        thinking_type = types.ThinkingConfig
    except AttributeError as exc:
        raise RuntimeError(
            "The installed google-genai package is too old for thinking controls. "
            "Update it with: pip install --upgrade google-genai"
        ) from exc

    if is_gemini_3:
        if normalized_mode == "dynamic":
            return None
        if normalized_mode == "off":
            level = "low" if "pro" in normalized_model else "minimal"
        elif normalized_mode == "minimal" and "pro" in normalized_model:
            level = "low"
        else:
            level = normalized_mode
        return thinking_type(thinking_level=level)

    if is_gemini_25:
        if normalized_mode == "dynamic":
            budget = -1
        elif normalized_mode == "off":
            # Gemini 2.5 Pro cannot fully disable thinking.
            budget = 128 if "pro" in normalized_model else 0
        else:
            budget = {
                "minimal": 512,
                "low": 1024,
                "medium": 8192,
                "high": 24576,
            }[normalized_mode]
        return thinking_type(thinking_budget=budget)
    return None


class GeminiApiClient:
    """Optional Gemini API client.

    This module does not require google-genai unless you instantiate this class.
    Set GEMINI_API_KEY in your environment or pass api_key directly.
    The actual translation prompt is SYSTEM_INSTRUCTIONS by default; override it
    with the ``prompt`` argument or the GUI Gemini API Prompt box.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gemini-2.5-flash",
        prompt: str | None = None,
        timeout_seconds: float = 90.0,
        thinking_mode: str = "off",
        max_output_tokens: int = 0,
    ):
        import os
        try:
            from google import genai  # type: ignore
            from google.genai import types  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Install google-genai to use GeminiApiClient: pip install google-genai") from exc
        resolved_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not resolved_key:
            raise RuntimeError("Gemini API key missing. Enter it in the AI Translation tab or set GEMINI_API_KEY.")
        try:
            resolved_timeout = max(1.0, float(timeout_seconds))
        except (TypeError, ValueError):
            resolved_timeout = 90.0
        timeout_ms = max(1, int(resolved_timeout * 1000))
        try:
            http_options = types.HttpOptions(timeout=timeout_ms)
        except Exception as exc:
            raise RuntimeError(
                "The installed google-genai package cannot configure request timeouts. "
                "Update it with: pip install --upgrade google-genai"
            ) from exc
        self._client = genai.Client(api_key=resolved_key, http_options=http_options)
        self._types = types
        self.model = model
        self.prompt = _clean_prompt(prompt)
        self.timeout_seconds = resolved_timeout
        self.thinking_mode = (thinking_mode or "off").strip().casefold()
        try:
            self.max_output_tokens = max(0, int(max_output_tokens))
        except (TypeError, ValueError):
            self.max_output_tokens = 0
        self.last_usage = GeminiApiUsage()
        self.total_usage = GeminiApiUsage()
        self.last_response_diagnostic = ""
        self._usage_lock = threading.Lock()

    def translate_payload(self, payload: dict[str, Any], prompt: str | None = None) -> dict[str, str]:
        expected_uids = [
            str(item.get("uid") or "")
            for item in payload.get("entries", [])
            if isinstance(item, Mapping) and str(item.get("uid") or "")
        ]
        active_prompt = _api_system_instruction(prompt or self.prompt)
        timeout_seconds = max(0.001, float(getattr(self, "timeout_seconds", 90.0)))
        configured_output_limit = max(0, int(getattr(self, "max_output_tokens", 0)))
        output_limit = configured_output_limit or _auto_max_output_tokens(payload)
        thinking = _thinking_config(
            self._types,
            str(getattr(self, "model", "")),
            str(getattr(self, "thinking_mode", "off")),
        )
        config_kwargs: dict[str, Any] = {
            "system_instruction": active_prompt,
            "response_mime_type": "application/json",
            "response_schema": _api_response_schema(len(expected_uids)),
            "max_output_tokens": output_limit,
            "temperature": 0,
        }
        if thinking is not None:
            config_kwargs["thinking_config"] = thinking
        completed = threading.Event()
        outcome: dict[str, Any] = {}

        def request() -> None:
            try:
                outcome["response"] = self._client.models.generate_content(
                    model=self.model,
                    contents=build_api_prompt(payload),
                    config=self._types.GenerateContentConfig(**config_kwargs),
                )
            except BaseException as exc:  # preserve the SDK's original exception for the caller
                outcome["error"] = exc
            finally:
                completed.set()

        request_thread = threading.Thread(target=request, name="gemini-api-request", daemon=True)
        request_thread.start()
        try:
            if not completed.wait(timeout_seconds):
                raise TimeoutError(
                    f"Gemini API request timed out after {timeout_seconds:g} seconds. "
                    "Check the connection, model name, and API status, or increase API timeout in the AI Translation tab."
                )
            error = outcome.get("error")
            if isinstance(error, BaseException):
                raise error
            response = outcome.get("response")
            if response is None:
                raise RuntimeError("Gemini API returned no response object.")
        except TimeoutError:
            raise
        except Exception as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            if "timeout" in detail.casefold() or "timed out" in detail.casefold():
                raise TimeoutError(
                    f"Gemini API request timed out after {timeout_seconds:g} seconds. "
                    "Check the connection, model name, and API status, or increase API timeout in the AI Translation tab."
                ) from exc
            raise RuntimeError(f"Gemini API request failed ({exc.__class__.__name__}): {detail}") from exc
        try:
            parsed = getattr(response, "parsed", None)
        except Exception:
            parsed = None
        if parsed is not None and not isinstance(parsed, (dict, list)):
            model_dump = getattr(parsed, "model_dump", None)
            if callable(model_dump):
                try:
                    parsed = model_dump()
                except Exception:
                    parsed = None
        usage = gemini_usage_from_response(response)
        with getattr(self, "_usage_lock", threading.Lock()):
            self.last_usage = usage
            total_usage = getattr(self, "total_usage", None)
            if not isinstance(total_usage, GeminiApiUsage):
                total_usage = GeminiApiUsage()
                self.total_usage = total_usage
            total_usage.add(usage)
        try:
            response_text = getattr(response, "text", "") or ""
        except Exception:
            response_text = ""
        response_value: Any = parsed if isinstance(parsed, (dict, list)) else response_text
        try:
            translations = parse_api_translation_response(response_value, expected_uids)
        except Exception as exc:
            diagnostic = _gemini_response_diagnostic(response, returned=0, mapped=0, expected=len(expected_uids))
            self.last_response_diagnostic = diagnostic
            detail = f" ({diagnostic})" if diagnostic else ""
            raise RuntimeError(f"Gemini returned an unreadable translation response{detail}: {exc}") from exc
        try:
            returned_items = len(_api_response_items(_response_data(response_value)))
        except Exception:
            returned_items = len(translations)
        diagnostic = _gemini_response_diagnostic(
            response,
            returned=returned_items,
            mapped=len(translations),
            expected=len(expected_uids),
        )
        self.last_response_diagnostic = diagnostic if len(translations) != len(expected_uids) else ""
        return translations


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
    cancel_check: Callable[[], None] | None = None,
) -> tuple[dict[str, str], list[TranslationError]]:
    """Translate entries with Gemini API.

    ``batch_size`` always controls how many current entries are sent per API
    request. When ``context_entries`` is supplied, each batch also receives up
    to ``context_limit`` entries immediately preceding the first current entry.
    This lets mass translation keep continuity without falling back to one API
    request per entry. Interactive callers use ``batch_size=1`` deliberately.
    """
    entry_list = list(entries)
    total_entries = len(entry_list)
    if progress is not None:
        progress(0, total_entries)
    all_errors: list[TranslationError] = []
    all_translations: dict[str, str] = {}

    def check_cancelled() -> None:
        if cancel_check is not None:
            cancel_check()

    def interruptible_sleep(seconds: float) -> None:
        if seconds <= 0:
            return
        if cancel_check is None:
            time.sleep(seconds)
            return
        deadline = time.monotonic() + seconds
        while True:
            check_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.1, remaining))

    check_cancelled()
    context_list = list(context_entries) if context_entries is not None else None
    effective_batch_size = max(1, int(batch_size))
    for i in range(0, total_entries, effective_batch_size):
        check_cancelled()
        batch = entry_list[i:i + effective_batch_size]
        previous_context: list[dict[str, Any]] = []
        if context_list is not None and batch:
            previous_context = build_previous_vietnamese_context(
                context_list,
                batch[0],
                limit=max(0, int(context_limit)),
                translation_overrides=all_translations,
                previous_file_entries=previous_file_context_entries,
            )
        payload = build_payload(
            batch,
            instructions=prompt or client.prompt,
            previous_vietnamese_context=previous_context,
        )
        translations = client.translate_payload(payload, prompt=prompt)
        check_cancelled()
        errors = validate_translations(batch, translations)
        response_diagnostic = str(getattr(client, "last_response_diagnostic", "") or "").strip()
        if response_diagnostic:
            for error in errors:
                if error.reason == "missing translation":
                    error.reason = f"missing translation ({response_diagnostic})"
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
            interruptible_sleep(sleep_seconds)
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
    cancel_check: Callable[[], None] | None = None,
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
        cancel_check=cancel_check,
    )
    changed = patch_msgstr_by_uid(po, translations)
    if changed:
        save_po(po, po_path)
    return changed, errors
