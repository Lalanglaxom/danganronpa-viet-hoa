from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Iterable

from .discovery import iter_po_files
from .models import POEntry, POFile, ReplacementChange, ReplacementRule
from .po_io import load_po, save_po
from .text_utils import search_replace_pairs

CLT_BLOCK_RE_TEMPLATE = r"(<CLT\s+{clt_id}>)(.*?)(<CLT>)"
TAG_RE = re.compile(r"<[^>]+>")


def _make_rule_id(find: str, replace: str, speaker: str | None, idx: int) -> str:
    """Create an internal log label; IDs are no longer stored in the rules file."""
    base = f"{speaker or 'global'}_{find}_to_{replace}".lower()
    base = re.sub(r"[^a-z0-9_]+", "_", base).strip("_")
    return f"{base or 'rule'}_{idx:03d}"


def _rule_from_dict(data: dict, idx: int) -> ReplacementRule:
    find = unicodedata.normalize("NFC", str(data.get("find", "")))
    replace = unicodedata.normalize("NFC", str(data.get("replace", "")))
    return ReplacementRule(
        # Kept only for change logs and backwards-compatible model usage.
        id=_make_rule_id(find, replace, data.get("speaker") or data.get("character"), idx),
        enabled=bool(data.get("enabled", True)),
        # Position is the priority now: weak rules are first, strong rules last.
        priority=idx,
        speaker=data.get("speaker", data.get("character")),
        scope=data.get("scope"),
        find=find,
        replace=replace,
        whole_word=bool(data.get("whole_word", False)),
        case_sensitive=bool(data.get("case_sensitive", True)),
        stop_after=bool(data.get("stop_after", False)),
        notes=str(data.get("notes", data.get("label", ""))),
    )


def _ordered_rule_items(raw: object) -> list[dict]:
    """Return rule dictionaries in weak-to-strong execution order.

    Version 3+ files already use list position. Older files with numeric
    priorities are migrated by sorting low priority first and high priority
    last, preserving file order for ties.
    """
    version = int(raw.get("version", 0) or 0) if isinstance(raw, dict) else 0
    items = raw.get("rules", raw) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    records = [(index, item) for index, item in enumerate(items) if isinstance(item, dict)]
    if version >= 3 or not any("priority" in item for _index, item in records):
        return [item for _index, item in records]

    def legacy_priority(record: tuple[int, dict]) -> tuple[int, int]:
        index, item = record
        try:
            value = int(item.get("priority", 100))
        except (TypeError, ValueError):
            value = 100
        return value, index

    return [item for _index, item in sorted(records, key=legacy_priority)]


def load_rules(path: str | Path) -> list[ReplacementRule]:
    """Load ordered rules or import the old mass-replace rules format."""
    p = Path(path)
    if not p.exists():
        return []
    raw = json.loads(p.read_text(encoding="utf-8"))
    items = _ordered_rule_items(raw)
    rules: list[ReplacementRule] = []
    idx = 0
    for item in items:
        # Legacy format: one criteria contains replace=[[find, replace], ...].
        legacy_pairs = item.get("replace")
        if isinstance(legacy_pairs, list) and legacy_pairs and isinstance(legacy_pairs[0], (list, tuple)):
            for pair in legacy_pairs:
                if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                    continue
                idx += 1
                rules.append(
                    _rule_from_dict(
                        {
                            "enabled": item.get("enabled", True),
                            "speaker": item.get("speaker", item.get("character")),
                            "scope": item.get("scope"),
                            "find": pair[0],
                            "replace": pair[1],
                            "whole_word": item.get("whole_word", False),
                            "case_sensitive": item.get("case_sensitive", True),
                            "stop_after": item.get("stop_after", False),
                            "notes": item.get("label", item.get("notes", "")),
                        },
                        idx,
                    )
                )
            continue
        idx += 1
        rules.append(_rule_from_dict(item, idx))
    return [rule for rule in rules if search_replace_pairs(rule.find, rule.replace)]


def save_rules(path: str | Path, rules: Iterable[ReplacementRule]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"version": 3, "rules": [rule_to_dict(rule) for rule in rules]}
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def rule_to_dict(rule: ReplacementRule) -> dict:
    """Serialize only user-editable rule fields; order supplies priority."""
    return {
        "enabled": rule.enabled,
        "speaker": rule.speaker or "",
        "scope": rule.scope or "",
        "find": rule.find,
        "replace": rule.replace,
        "whole_word": rule.whole_word,
        "case_sensitive": rule.case_sensitive,
        "stop_after": rule.stop_after,
        "notes": rule.notes,
    }


def _speaker_matches(entry: POEntry, rule: ReplacementRule) -> bool:
    if not rule.speaker:
        return True
    return rule.speaker.upper() in (entry.msgctxt or "").upper()


def _compile_find(find: str, whole_word: bool, case_sensitive: bool) -> re.Pattern[str]:
    pattern = re.escape(find)
    if whole_word:
        pattern = r"(?<!\w)" + pattern + r"(?!\w)"
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(pattern, flags)


def _replace_pairs_in_plain_text(text: str, rule: ReplacementRule) -> tuple[str, int]:
    total = 0
    updated = text
    for find, replace in search_replace_pairs(rule.find, rule.replace):
        pattern = _compile_find(find, rule.whole_word, rule.case_sensitive)
        updated, count = pattern.subn(lambda _match, value=replace: value, updated)
        total += count
    return updated, total


def _replace_outside_tags(text: str, rule: ReplacementRule) -> tuple[str, int]:
    total = 0
    result: list[str] = []
    last = 0
    for tag in TAG_RE.finditer(text):
        chunk, count = _replace_pairs_in_plain_text(text[last:tag.start()], rule)
        total += count
        result.append(chunk)
        result.append(tag.group(0))
        last = tag.end()
    tail, count = _replace_pairs_in_plain_text(text[last:], rule)
    total += count
    result.append(tail)
    return "".join(result), total


def _replace_in_scope(text: str, rule: ReplacementRule) -> tuple[str, int]:
    if not rule.scope or not rule.scope.lower().startswith("clt:"):
        return _replace_outside_tags(text, rule)
    clt_id = rule.scope.split(":", 1)[1].strip()
    if not clt_id:
        return text, 0
    block_re = re.compile(CLT_BLOCK_RE_TEMPLATE.format(clt_id=re.escape(clt_id)), re.DOTALL | re.IGNORECASE)
    total = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal total
        start, content, end = match.groups()
        new_content, count = _replace_outside_tags(content, rule)
        total += count
        return start + new_content + end

    return block_re.sub(repl, text), total


def apply_rules_to_entry(entry: POEntry, rules: Iterable[ReplacementRule]) -> tuple[str, list[tuple[ReplacementRule, int, str, str]]]:
    """Apply enabled rules from top to bottom; lower rules are stronger."""
    text = entry.msgstr
    hits: list[tuple[ReplacementRule, int, str, str]] = []
    for rule in (candidate for candidate in rules if candidate.enabled):
        if not _speaker_matches(entry, rule):
            continue
        before = text
        text, count = _replace_in_scope(text, rule)
        if count:
            hits.append((rule, count, before, text))
            if rule.stop_after:
                break
    return text, hits


def apply_rules_to_po(po_file: POFile, rules: Iterable[ReplacementRule], file_path: Path | None = None) -> list[ReplacementChange]:
    changes: list[ReplacementChange] = []
    fpath = file_path or po_file.path or Path("")
    rules_list = list(rules)
    for entry in po_file.entries:
        new_msgstr, hits = apply_rules_to_entry(entry, rules_list)
        if not hits:
            continue
        for rule, count, before, after in hits:
            changes.append(
                ReplacementChange(
                    file=fpath,
                    uid=entry.uid,
                    msgctxt=entry.msgctxt or "",
                    rule_id=rule.id,
                    before=before,
                    after=after,
                    count=count,
                )
            )
        entry.msgstr = new_msgstr
    return changes


def apply_rules_to_file(path: str | Path, rules: Iterable[ReplacementRule], dry_run: bool = False) -> list[ReplacementChange]:
    po = load_po(path)
    changes = apply_rules_to_po(po, rules, Path(path))
    if changes and not dry_run:
        save_po(po, path)
    return changes


def apply_rules_to_path(path: str | Path, rules: Iterable[ReplacementRule], dry_run: bool = False) -> list[ReplacementChange]:
    all_changes: list[ReplacementChange] = []
    rules_list = list(rules)
    for po_path in iter_po_files(path):
        all_changes.extend(apply_rules_to_file(po_path, rules_list, dry_run=dry_run))
    return all_changes
