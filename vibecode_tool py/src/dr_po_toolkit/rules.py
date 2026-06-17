from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Iterable

from .discovery import iter_po_files
from .models import POEntry, POFile, ReplacementChange, ReplacementRule
from .po_io import load_po, save_po

CLT_BLOCK_RE_TEMPLATE = r"(<CLT\s+{clt_id}>)(.*?)(<CLT>)"
TAG_RE = re.compile(r"<[^>]+>")


def _make_rule_id(find: str, replace: str, speaker: str | None, idx: int) -> str:
    base = f"{speaker or 'global'}_{find}_to_{replace}".lower()
    base = re.sub(r"[^a-z0-9_]+", "_", base).strip("_")
    return f"{base or 'rule'}_{idx:03d}"


def _rule_from_dict(data: dict, idx: int) -> ReplacementRule:
    return ReplacementRule(
        id=str(data.get("id") or data.get("label") or _make_rule_id(str(data.get("find", "")), str(data.get("replace", "")), data.get("speaker") or data.get("character"), idx)),
        enabled=bool(data.get("enabled", True)),
        priority=int(data.get("priority", 100)),
        speaker=data.get("speaker", data.get("character")),
        scope=data.get("scope"),
        find=unicodedata.normalize("NFC", str(data.get("find", ""))),
        replace=unicodedata.normalize("NFC", str(data.get("replace", ""))),
        whole_word=bool(data.get("whole_word", False)),
        case_sensitive=bool(data.get("case_sensitive", True)),
        stop_after=bool(data.get("stop_after", False)),
        notes=str(data.get("notes", "")),
    )


def load_rules(path: str | Path) -> list[ReplacementRule]:
    """Load new rules format or import the old mass_replace_rules.json format."""
    p = Path(path)
    if not p.exists():
        return []
    raw = json.loads(p.read_text(encoding="utf-8"))
    items = raw.get("rules", raw) if isinstance(raw, dict) else raw
    rules: list[ReplacementRule] = []
    idx = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        # Legacy format: one criteria contains replace=[[find, replace], ...]
        if "replace" in item and isinstance(item["replace"], list) and item["replace"] and isinstance(item["replace"][0], list):
            for pair in item.get("replace", []):
                if not isinstance(pair, list | tuple) or len(pair) != 2:
                    continue
                idx += 1
                rules.append(
                    ReplacementRule(
                        id=_make_rule_id(str(pair[0]), str(pair[1]), item.get("character"), idx),
                        enabled=bool(item.get("enabled", True)),
                        priority=int(item.get("priority", 100)),
                        speaker=item.get("speaker", item.get("character")),
                        scope=item.get("scope"),
                        find=unicodedata.normalize("NFC", str(pair[0])),
                        replace=unicodedata.normalize("NFC", str(pair[1])),
                        whole_word=bool(item.get("whole_word", False)),
                        case_sensitive=bool(item.get("case_sensitive", True)),
                        stop_after=bool(item.get("stop_after", False)),
                        notes=str(item.get("label", item.get("notes", ""))),
                    )
                )
            continue
        idx += 1
        rules.append(_rule_from_dict(item, idx))
    return sorted([r for r in rules if r.find], key=lambda r: (-r.priority, r.id))


def save_rules(path: str | Path, rules: Iterable[ReplacementRule]) -> None:
    p = Path(path)
    data = {"version": 2, "rules": [rule_to_dict(r) for r in rules]}
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def rule_to_dict(rule: ReplacementRule) -> dict:
    return {
        "id": rule.id,
        "enabled": rule.enabled,
        "priority": rule.priority,
        "speaker": rule.speaker,
        "scope": rule.scope,
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


def _replace_outside_tags(text: str, rule: ReplacementRule) -> tuple[str, int]:
    pattern = _compile_find(rule.find, rule.whole_word, rule.case_sensitive)
    total = 0
    result: list[str] = []
    last = 0
    for tag in TAG_RE.finditer(text):
        chunk = text[last:tag.start()]
        new_chunk, n = pattern.subn(rule.replace, chunk)
        total += n
        result.append(new_chunk)
        result.append(tag.group(0))
        last = tag.end()
    tail, n = pattern.subn(rule.replace, text[last:])
    total += n
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
        new_content, n = _replace_outside_tags(content, rule)
        total += n
        return start + new_content + end

    return block_re.sub(repl, text), total


def apply_rules_to_entry(entry: POEntry, rules: Iterable[ReplacementRule]) -> tuple[str, list[tuple[ReplacementRule, int, str, str]]]:
    text = entry.msgstr
    hits: list[tuple[ReplacementRule, int, str, str]] = []
    for rule in sorted((r for r in rules if r.enabled), key=lambda r: (-r.priority, r.id)):
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
    for entry in po_file.entries:
        new_msgstr, hits = apply_rules_to_entry(entry, rules)
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
    for po_path in iter_po_files(path):
        all_changes.extend(apply_rules_to_file(po_path, rules, dry_run=dry_run))
    return all_changes
