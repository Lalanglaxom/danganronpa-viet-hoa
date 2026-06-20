from __future__ import annotations

import html
import re
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .discovery import SegmentFiles, find_backup_for_file, find_segments, iter_po_files
from .models import POEntry, POFile, ValidationIssue
from .po_io import load_po
from .text_utils import clt_tags, generic_tags, has_bad_unicode, order_number, placeholders_by_type

LEVEL_ORDER = {"ERROR": 0, "WARN": 1, "STRUCT": 2, "INFO": 3}


def _issue(level: str, check: str, detail: str, file: Path | None = None, entry: POEntry | None = None) -> ValidationIssue:
    return ValidationIssue(
        level=level,
        check=check,
        detail=detail,
        file=file,
        msgctxt=entry.msgctxt if entry else None,
        line=entry.line if entry else 0,
    )


def _entry_map(po: POFile) -> dict[str, POEntry]:
    return {e.msgctxt: e for e in po.entries if e.msgctxt is not None}


def _entry_order(po: POFile) -> list[str]:
    return [e.msgctxt or "" for e in po.entries]


def validate_po_pair(work_path: str | Path, copy_path: str | Path | None = None) -> list[ValidationIssue]:
    work_path = Path(work_path)
    copy_path = Path(copy_path) if copy_path else find_backup_for_file(work_path)
    issues: list[ValidationIssue] = []

    try:
        work = load_po(work_path)
    except Exception as exc:
        return [_issue("ERROR", "parse", f"Cannot parse working PO: {exc}", work_path)]

    for parse_issue in work.issues:
        issues.append(_issue(parse_issue.level, "parse", parse_issue.message, work_path))

    if copy_path is None or not copy_path.exists():
        issues.append(_issue("ERROR", "backup", "Copy.po backup not found", work_path))
        copy = None
    else:
        try:
            copy = load_po(copy_path)
        except Exception as exc:
            copy = None
            issues.append(_issue("ERROR", "parse", f"Cannot parse Copy.po: {exc}", copy_path))

    if copy is not None:
        for parse_issue in copy.issues:
            issues.append(_issue(parse_issue.level, "copy_parse", parse_issue.message, copy_path))
        issues.extend(_validate_pair_structure(copy, work, work_path))

    issues.extend(_validate_single_file(work, work_path))
    return sorted(issues, key=lambda x: (LEVEL_ORDER.get(x.level, 9), x.check, x.line, x.detail))


def _validate_pair_structure(copy: POFile, work: POFile, work_path: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    copy_map = _entry_map(copy)
    work_map = _entry_map(work)
    copy_keys = set(copy_map)
    work_keys = set(work_map)

    copy_order = _entry_order(copy)
    work_order = _entry_order(work)
    if copy_order != work_order:
        first_diff = None
        for i, (c, w) in enumerate(zip(copy_order, work_order)):
            if c != w:
                first_diff = i
                break
        if first_diff is None:
            detail = f"Entry order length mismatch: Copy={len(copy_order)}, Working={len(work_order)}"
        else:
            detail = f'First order mismatch at position {first_diff + 1}: Copy="{copy_order[first_diff]}", Working="{work_order[first_diff]}"'
        issues.append(_issue("ERROR", "entry_order", detail, work_path))

    if len(copy_map) != len(work_map):
        issues.append(_issue("ERROR", "entry_count", f"Entry count mismatch: Copy={len(copy_map)}, Working={len(work_map)}", work_path))

    for key in sorted(copy_keys - work_keys):
        issues.append(_issue("ERROR", "missing_entry", f'"{key}" exists in Copy but is missing in Working', work_path))
    for key in sorted(work_keys - copy_keys):
        issues.append(_issue("WARN", "extra_entry", f'"{key}" exists in Working but not in Copy', work_path, work_map[key]))

    for key in sorted(copy_keys & work_keys):
        c = copy_map[key]
        w = work_map[key]
        if _normalize_msgid(c.msgid) != _normalize_msgid(w.msgid):
            issues.append(
                _issue(
                    "ERROR",
                    "source_changed",
                    f'English source changed for "{key}"\nCopy: {c.msgid[:160]!r}\nWork: {w.msgid[:160]!r}',
                    work_path,
                    w,
                )
            )
    return issues


def _normalize_msgid(text: str) -> str:
    text = text.replace("\\n", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip().lower()
    return re.sub(r"[.,?!\-]+$", "", text).strip()


def _validate_single_file(po: POFile, path: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    dupes = po.duplicate_contexts()
    for ctx, entries in dupes.items():
        lines = ", ".join(str(e.line) for e in entries)
        issues.append(_issue("ERROR", "duplicate_msgctxt", f'duplicate msgctxt "{ctx}" at lines {lines}', path, entries[-1]))

    orders: dict[int, POEntry] = {}
    for e in po.entries:
        num = order_number(e.msgctxt)
        if num is None:
            continue
        if num in orders:
            issues.append(_issue("ERROR", "duplicate_order_number", f"duplicate leading order number {num}; first line {orders[num].line}", path, e))
        else:
            orders[num] = e

    untranslated = [e for e in po.entries if not e.msgstr.strip()]
    if untranslated:
        preview = ", ".join((e.msgctxt or e.uid) for e in untranslated[:20])
        suffix = " ..." if len(untranslated) > 20 else ""
        issues.append(_issue("WARN", "untranslated", f"{len(untranslated)} untranslated entries: {preview}{suffix}", path))

    for e in po.entries:
        if not e.msgstr:
            continue
        issues.extend(_validate_entry(e, path, po))

    # Repeated source with different non-empty translations.
    repeated: dict[str, list[POEntry]] = defaultdict(list)
    for e in po.entries:
        src = e.msgid.strip()
        if src and e.msgstr.strip():
            repeated[src].append(e)
    for src, group in repeated.items():
        translations: dict[str, list[POEntry]] = defaultdict(list)
        for e in group:
            translations[e.msgstr.strip()].append(e)
        if len(translations) > 1:
            samples = []
            for translation, entries in list(translations.items())[:4]:
                where = ", ".join((x.msgctxt or x.uid) for x in entries[:3])
                samples.append(f"{translation!r} -> {where}")
            issues.append(_issue("WARN", "repeated_source_inconsistent", " | ".join(samples), path, group[0]))

    return issues


def _validate_entry(entry: POEntry, path: Path, po: POFile) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if entry.msgid.strip() and entry.msgid.strip() == entry.msgstr.strip():
        issues.append(_issue("INFO", "copy_of_source", "msgstr is identical to msgid", path, entry))

    src_clt = clt_tags(entry.msgid)
    tgt_clt = clt_tags(entry.msgstr)
    if src_clt != tgt_clt:
        issues.append(_issue("ERROR", "clt_tag_integrity", f"CLT tags differ. source={src_clt!r}, translation={tgt_clt!r}", path, entry))

    src_tags = generic_tags(entry.msgid)
    tgt_tags = generic_tags(entry.msgstr)
    if src_tags != tgt_tags:
        issues.append(_issue("WARN", "generic_tag_integrity", f"Generic tags differ. source={src_tags!r}, translation={tgt_tags!r}", path, entry))

    for label, src_tokens in placeholders_by_type(entry.msgid).items():
        tgt_tokens = placeholders_by_type(entry.msgstr)[label]
        if sorted(src_tokens) != sorted(tgt_tokens):
            issues.append(_issue("ERROR", "placeholder_integrity", f"{label} placeholders differ. source={src_tokens!r}, translation={tgt_tokens!r}", path, entry))

    if entry.msgstr != unicodedata.normalize("NFC", entry.msgstr):
        issues.append(_issue("WARN", "unicode_nfc", "msgstr is not NFC-normalized", path, entry))

    bad_unicode = has_bad_unicode(entry.msgstr)
    if bad_unicode:
        issues.append(_issue("WARN", "unicode_control", ", ".join(bad_unicode), path, entry))

    suspicious = _suspicious_whitespace(entry.msgstr)
    for detail in suspicious:
        issues.append(_issue("WARN", "whitespace", detail, path, entry))

    has_choice_react = any("CHOICE/RE:ACT" in (x.msgctxt or "") for x in po.entries)
    if has_choice_react and entry.msgid.count("\n") != entry.msgstr.count("\n") and entry.msgid.count("\n") > 0:
        issues.append(
            _issue(
                "STRUCT",
                "line_count_match",
                f"msgid has {entry.msgid.count('\n')} newline separators; msgstr has {entry.msgstr.count('\n')}",
                path,
                entry,
            )
        )

    return issues


def _suspicious_whitespace(text: str) -> list[str]:
    hidden = {
        "\u00A0": "NBSP",
        "\u200B": "ZERO WIDTH SPACE",
        "\u200C": "ZERO WIDTH NON-JOINER",
        "\u200D": "ZERO WIDTH JOINER",
        "\u2060": "WORD JOINER",
        "\u3000": "FULL-WIDTH SPACE",
        "\t": "TAB",
    }
    issues: list[str] = []
    for ch, name in hidden.items():
        if ch in text:
            issues.append(f"contains {name}")
    if text != text.strip():
        issues.append("leading or trailing whitespace in msgstr")
    if "  " in text:
        issues.append("contains double spaces")
    return issues


def validate_path(path: str | Path) -> dict[Path, list[ValidationIssue]]:
    p = Path(path)
    results: dict[Path, list[ValidationIssue]] = {}
    if p.is_file():
        results[p] = validate_po_pair(p)
        return results

    found_segment = False
    for seg in find_segments(p):
        found_segment = True
        results[seg.work_po] = validate_po_pair(seg.work_po, seg.copy_po)
    if not found_segment:
        for po_path in iter_po_files(p):
            results[po_path] = validate_po_pair(po_path)
    return results


def format_text_report(results: dict[Path, list[ValidationIssue]], root: str | Path | None = None) -> str:
    root_path = Path(root).resolve() if root else None
    lines = ["=" * 72, "PO VALIDATION REPORT", f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}", "=" * 72]
    total = {"ERROR": 0, "WARN": 0, "STRUCT": 0, "INFO": 0}
    for path, issues in sorted(results.items(), key=lambda x: str(x[0])):
        display = str(path)
        if root_path:
            try:
                display = str(path.resolve().relative_to(root_path))
            except Exception:
                pass
        status = "OK" if not issues else ", ".join(f"{lvl}:{sum(1 for i in issues if i.level == lvl)}" for lvl in ["ERROR", "WARN", "STRUCT", "INFO"] if any(i.level == lvl for i in issues))
        lines.append(f"\n[{status}] {display}")
        for issue in issues:
            total[issue.level] = total.get(issue.level, 0) + 1
            loc = f" line {issue.line}" if issue.line else ""
            ctx = f" {issue.msgctxt}" if issue.msgctxt else ""
            lines.append(f"  {issue.level:<6} [{issue.check}]{loc}{ctx}: {issue.detail}")
    lines.extend(["", "=" * 72, "SUMMARY"])
    lines.append(f"Files checked: {len(results)}")
    for lvl in ["ERROR", "WARN", "STRUCT", "INFO"]:
        lines.append(f"{lvl}: {total.get(lvl, 0)}")
    return "\n".join(lines) + "\n"


def _delete_old_validation_reports(out_dir: Path) -> int:
    """Delete reports generated by earlier validator runs in this output folder."""
    deleted = 0
    for pattern in ("validation_*.log", "validation_*.html"):
        for old_report in out_dir.glob(pattern):
            try:
                if old_report.is_file():
                    old_report.unlink()
                    deleted += 1
            except OSError:
                # Do not block a new report if an old file is locked.
                continue
    return deleted


def write_reports(results: dict[Path, list[ValidationIssue]], out_dir: str | Path, root: str | Path | None = None) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _delete_old_validation_reports(out)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    txt = out / f"validation_{stamp}.log"
    html_path = out / f"validation_{stamp}.html"
    txt.write_text(format_text_report(results, root), encoding="utf-8")
    html_path.write_text(build_html_report(results, root), encoding="utf-8")
    return txt, html_path


def build_html_report(results: dict[Path, list[ValidationIssue]], root: str | Path | None = None) -> str:
    rows: list[str] = []
    root_path = Path(root).resolve() if root else None
    for path, issues in sorted(results.items(), key=lambda x: str(x[0])):
        display = str(path)
        if root_path:
            try:
                display = str(path.resolve().relative_to(root_path))
            except Exception:
                pass
        status = "ok" if not issues else ("error" if any(i.level == "ERROR" for i in issues) else "warn")
        issue_html = "".join(
            f'<div class="issue {html.escape(i.level.lower())}"><b>{html.escape(i.level)} [{html.escape(i.check)}]</b> '
            f'{html.escape(i.detail)} <span>{html.escape(i.msgctxt or "")}</span></div>'
            for i in issues
        ) or '<div class="issue ok">No issues.</div>'
        rows.append(f'<section class="file {status}"><h2>{html.escape(display)}</h2>{issue_html}</section>')
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>PO Validation Report</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;background:#f6f7fb;color:#1d1d24;margin:0;padding:24px}}
.file{{background:white;border:1px solid #ddd;border-radius:8px;margin:0 0 12px;padding:12px 16px}}
.file.error{{border-left:6px solid #d43c31}} .file.warn{{border-left:6px solid #d69d00}} .file.ok{{border-left:6px solid #168a48}}
h1{{margin:0 0 16px}} h2{{font-size:15px;margin:0 0 8px}}
.issue{{font-size:13px;line-height:1.45;margin:4px 0;padding:6px 8px;background:#fafafa;border-radius:4px;white-space:pre-wrap}}
.issue.error{{background:#fff1f0}} .issue.warn{{background:#fffbe6}} .issue.info{{background:#eef4ff}} .issue.struct{{background:#f1fff6}}
span{{color:#666;font-family:Consolas,monospace}}
</style></head><body><h1>PO Validation Report</h1>{''.join(rows)}</body></html>"""
