from __future__ import annotations

import html
import re
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from .app_links import build_entry_url
from .discovery import SegmentFiles, find_backup_for_file, find_segments, iter_po_files
from .models import POEntry, POFile, ValidationIssue
from .po_io import load_po
from .text_utils import clt_tags, generic_tags, has_bad_unicode, order_number, placeholders_by_type, visible_text

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


def _normalize_duplicate_sentence(text: str) -> str:
    text = visible_text(text).replace("\\n", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip().casefold()
    return re.sub(r"[.!?…。！？\-]+$", "", text).strip()


def _normalize_duplicate_translation(text: str) -> str:
    text = visible_text(text).replace("\\n", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip().casefold()


def _short(text: str, limit: int = 140) -> str:
    clean = visible_text(text).replace("\n", " ")
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) > limit:
        return clean[: limit - 1].rstrip() + "…"
    return clean


def _validate_duplicate_sentence_translations(
    entries: Iterable[POEntry],
    path: Path,
    *,
    check_name: str,
    global_locations: dict[int, str] | None = None,
) -> list[ValidationIssue]:
    """Detect same source sentence translated differently.

    Uses normalized visible msgid text so duplicates are caught even if line breaks,
    CLT tags, bracket tags, or spacing differ.
    """
    issues: list[ValidationIssue] = []
    repeated: dict[str, list[POEntry]] = defaultdict(list)
    for e in entries:
        src_key = _normalize_duplicate_sentence(e.msgid)
        if src_key and e.msgstr.strip():
            repeated[src_key].append(e)

    for _src_key, group in repeated.items():
        translations: dict[str, list[POEntry]] = defaultdict(list)
        for e in group:
            trans_key = _normalize_duplicate_translation(e.msgstr)
            if trans_key:
                translations[trans_key].append(e)
        if len(translations) <= 1:
            continue

        source_preview = _short(group[0].msgid, 180)
        samples: list[str] = []
        for _translation_key, t_entries in list(translations.items())[:8]:
            sample_entry = t_entries[0]
            where_parts: list[str] = []
            for x in t_entries[:3]:
                global_where = global_locations.get(id(x), "") if global_locations else ""
                if global_where:
                    where_parts.append(global_where)
                else:
                    where_parts.append(f"line {x.line} {x.msgctxt or x.uid}".strip())
            if len(t_entries) > 3:
                where_parts.append(f"+{len(t_entries) - 3} more")
            samples.append(f"{_short(sample_entry.msgstr, 120)!r} -> {', '.join(where_parts)}")
        detail = f"Same msgid has different msgstr. msgid={source_preview!r}. " + " | ".join(samples)

        # Add one issue for the first entry of each different translation so the
        # HTML report can jump/filter to every conflicting variant without
        # flooding every repeated identical line.
        for t_entries in translations.values():
            if t_entries:
                issues.append(_issue("WARN", check_name, detail, path, t_entries[0]))
    return issues


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

    issues.extend(_validate_duplicate_sentence_translations(po.entries, path, check_name="duplicate_sentence_translation"))

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


def _append_cross_file_duplicate_sentence_translation_issues(results: dict[Path, list[ValidationIssue]]) -> None:
    """Add folder-level duplicate msgid/different msgstr warnings across PO files."""
    if len(results) <= 1:
        return

    repeated: dict[str, list[tuple[Path, POEntry]]] = defaultdict(list)
    for path in list(results):
        try:
            po = load_po(path)
        except Exception:
            continue
        for entry in po.entries:
            src_key = _normalize_duplicate_sentence(entry.msgid)
            if src_key and entry.msgstr.strip():
                repeated[src_key].append((path, entry))

    for _src_key, group in repeated.items():
        involved_files = {path for path, _entry in group}
        if len(involved_files) <= 1:
            continue

        translations: dict[str, list[tuple[Path, POEntry]]] = defaultdict(list)
        for path, entry in group:
            trans_key = _normalize_duplicate_translation(entry.msgstr)
            if trans_key:
                translations[trans_key].append((path, entry))
        if len(translations) <= 1:
            continue

        source_preview = _short(group[0][1].msgid, 180)
        samples: list[str] = []
        for _translation_key, t_items in list(translations.items())[:10]:
            sample_path, sample_entry = t_items[0]
            where_parts: list[str] = []
            for path, entry in t_items[:4]:
                where_parts.append(f"{path.name}: line {entry.line} {entry.msgctxt or entry.uid}".strip())
            if len(t_items) > 4:
                where_parts.append(f"+{len(t_items) - 4} more")
            samples.append(f"{_short(sample_entry.msgstr, 120)!r} -> {', '.join(where_parts)}")
        detail = f"Same msgid has different msgstr across files. msgid={source_preview!r}. " + " | ".join(samples)

        # Put one issue on the first entry of each translation variant.
        for t_items in translations.values():
            if not t_items:
                continue
            issue_path, issue_entry = t_items[0]
            results.setdefault(issue_path, []).append(
                _issue("WARN", "duplicate_sentence_translation_global", detail, issue_path, issue_entry)
            )

    for path, issues in results.items():
        issues.sort(key=lambda x: (LEVEL_ORDER.get(x.level, 9), x.check, x.line, x.detail))


def validate_path(
    path: str | Path,
    *,
    progress: Callable[[int, int, Path], None] | None = None,
) -> dict[Path, list[ValidationIssue]]:
    p = Path(path)
    results: dict[Path, list[ValidationIssue]] = {}
    if p.is_file():
        if progress is not None:
            progress(0, 1, p)
        results[p] = validate_po_pair(p)
        if progress is not None:
            progress(1, 1, p)
        return results

    segments = list(find_segments(p))
    if segments:
        total = len(segments)
        if progress is not None:
            progress(0, total, segments[0].work_po)
        for index, seg in enumerate(segments, start=1):
            results[seg.work_po] = validate_po_pair(seg.work_po, seg.copy_po)
            if progress is not None:
                progress(index, total, seg.work_po)
    else:
        po_files = list(iter_po_files(p))
        total = len(po_files)
        if progress is not None and po_files:
            progress(0, total, po_files[0])
        for index, po_path in enumerate(po_files, start=1):
            results[po_path] = validate_po_pair(po_path)
            if progress is not None:
                progress(index, total, po_path)

    _append_cross_file_duplicate_sentence_translation_issues(results)
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
    """Delete current and legacy reports generated by earlier validator runs."""
    deleted = 0
    reports: set[Path] = {
        out_dir / "validation.log",
        out_dir / "validation.html",
    }
    for pattern in ("validation_*.log", "validation_*.html"):
        reports.update(out_dir.glob(pattern))

    for old_report in reports:
        try:
            if old_report.is_file():
                old_report.unlink()
                deleted += 1
        except OSError:
            # A legacy report may be locked by another program. Fixed output
            # names below still prevent each new validation run adding files.
            continue
    return deleted


def write_reports(results: dict[Path, list[ValidationIssue]], out_dir: str | Path, root: str | Path | None = None) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _delete_old_validation_reports(out)

    # Reuse the same two files on every run instead of creating timestamped
    # reports that slowly fill the validated folder.
    txt = out / "validation.log"
    html_path = out / "validation.html"
    txt.write_text(format_text_report(results, root), encoding="utf-8")
    html_path.write_text(build_html_report(results, root), encoding="utf-8")
    return txt, html_path


def build_html_report(results: dict[Path, list[ValidationIssue]], root: str | Path | None = None) -> str:
    rows: list[str] = []
    root_path = Path(root).resolve() if root else None
    levels = ["ERROR", "WARN", "STRUCT", "INFO"]
    total_by_level = {level: 0 for level in levels}
    check_counts: dict[str, int] = defaultdict(int)
    total_issues = 0

    def display_path(path: Path) -> str:
        display = str(path)
        if root_path:
            try:
                display = str(path.resolve().relative_to(root_path))
            except Exception:
                pass
        return display

    for _path, issues in results.items():
        for issue in issues:
            total_issues += 1
            total_by_level[issue.level] = total_by_level.get(issue.level, 0) + 1
            check_counts[issue.check] += 1

    check_options = ['<option value="">All checks</option>']
    for check, count in sorted(check_counts.items(), key=lambda item: (item[0].lower(), item[1])):
        value = html.escape(check, quote=True)
        check_options.append(f'<option value="{value}">{html.escape(check)} ({count})</option>')

    level_options = ['<option value="">All levels</option>']
    for level in levels:
        value = html.escape(level, quote=True)
        level_options.append(f'<option value="{value}">{html.escape(level)} ({total_by_level.get(level, 0)})</option>')
    level_options.append('<option value="OK">OK files</option>')

    summary_cards = "".join(
        f'<button class="summary-card" type="button" data-set-level="{html.escape(level, quote=True)}">'
        f'<b>{html.escape(level)}</b><span>{total_by_level.get(level, 0)}</span></button>'
        for level in levels
    )
    summary_cards += (
        f'<button class="summary-card" type="button" data-clear-filters="1"><b>FILES</b><span>{len(results)}</span></button>'
        f'<button class="summary-card" type="button" data-clear-filters="1"><b>ISSUES</b><span>{total_issues}</span></button>'
    )

    for path, issues in sorted(results.items(), key=lambda x: str(x[0])):
        display = display_path(path)
        status = "ok" if not issues else ("error" if any(i.level == "ERROR" for i in issues) else "warn")
        file_counts = {level: sum(1 for i in issues if i.level == level) for level in levels}
        count_text = "OK" if not issues else " · ".join(f"{level}:{count}" for level, count in file_counts.items() if count)
        issue_blocks: list[str] = []
        if issues:
            for i in issues:
                level = i.level.upper()
                check = i.check
                context = i.msgctxt or ""
                line_text = f"line {i.line}" if i.line else ""
                searchable = " ".join([display, level, check, context, line_text, i.detail]).casefold()
                open_url = build_entry_url(path, context=context, line=i.line)
                issue_blocks.append(
                    f'<div class="issue {html.escape(level.lower(), quote=True)}" '
                    f'data-level="{html.escape(level, quote=True)}" '
                    f'data-check="{html.escape(check, quote=True)}" '
                    f'data-context="{html.escape(context.casefold(), quote=True)}" '
                    f'data-detail="{html.escape(searchable, quote=True)}">'
                    f'<div class="issue-head"><b>{html.escape(level)} [{html.escape(check)}]</b> '
                    f'<span class="line">{html.escape(line_text)}</span> '
                    f'<span class="ctx">{html.escape(context)}</span>'
                    f'<a class="open-entry" href="{html.escape(open_url, quote=True)}" title="Open this entry in DR PO Toolkit">Open in app</a></div>'
                    f'<div class="detail">{html.escape(i.detail)}</div>'
                    f'</div>'
                )
        else:
            issue_blocks.append('<div class="issue ok" data-level="OK" data-check="ok" data-context="" data-detail="ok">No issues.</div>')

        rows.append(
            f'<section class="file {status}" data-file="{html.escape(display.casefold(), quote=True)}" '
            f'data-status="{html.escape(status, quote=True)}" data-issue-count="{len(issues)}">'
            f'<h2>{html.escape(display)} <span class="file-count">{html.escape(count_text)}</span></h2>'
            f'{"".join(issue_blocks)}</section>'
        )

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>PO Validation Report</title>
<style>
:root{{--bg:#f6f7fb;--ink:#1d1d24;--muted:#666;--panel:#fff;--line:#ddd;--red:#d43c31;--yellow:#d69d00;--green:#168a48;--blue:#2b65d9}}
*{{box-sizing:border-box}}
body{{font-family:Segoe UI,Arial,sans-serif;background:var(--bg);color:var(--ink);margin:0;padding:20px}}
h1{{margin:0 0 12px;font-size:24px}}
.toolbar{{position:sticky;top:0;z-index:5;background:rgba(246,247,251,.97);backdrop-filter:blur(6px);border-bottom:1px solid var(--line);padding:0 0 14px;margin:0 0 14px}}
.summary{{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 12px}}
.summary-card{{border:1px solid var(--line);background:#fff;border-radius:8px;padding:8px 12px;cursor:pointer;display:flex;gap:8px;align-items:center}}
.summary-card span{{font-family:Consolas,monospace;color:var(--blue);font-weight:700}}
.filters{{display:grid;grid-template-columns:repeat(5,minmax(140px,1fr));gap:8px;align-items:center}}
.filters input,.filters select{{width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;background:white;color:var(--ink)}}
.filters label{{display:flex;gap:6px;align-items:center;white-space:nowrap;font-size:13px}}
#visibleCount{{font-size:13px;color:var(--muted);padding:8px 0}}
.file{{background:var(--panel);border:1px solid var(--line);border-radius:8px;margin:0 0 12px;padding:12px 16px}}
.file.error{{border-left:6px solid var(--red)}} .file.warn{{border-left:6px solid var(--yellow)}} .file.ok{{border-left:6px solid var(--green)}}
h2{{font-size:15px;margin:0 0 8px;word-break:break-word}}
.file-count{{color:var(--muted);font-size:12px;font-family:Consolas,monospace;margin-left:8px}}
.issue{{font-size:13px;line-height:1.45;margin:6px 0;padding:8px 10px;background:#fafafa;border-radius:5px;white-space:pre-wrap;border:1px solid transparent}}
.issue-head{{display:flex;gap:10px;flex-wrap:wrap;align-items:baseline;margin-bottom:3px}}
.issue.error{{background:#fff1f0;border-color:#ffd3cf}} .issue.warn{{background:#fffbe6;border-color:#f3df91}} .issue.info{{background:#eef4ff;border-color:#cbdcff}} .issue.struct{{background:#f1fff6;border-color:#c9edd4}} .issue.ok{{background:#effaf2;border-color:#c9e8d2}}
.line,.ctx{{color:var(--muted);font-family:Consolas,monospace}}
.open-entry{{margin-left:auto;color:var(--blue);font-weight:700;text-decoration:none;border:1px solid #bfd0f7;border-radius:5px;padding:2px 7px;background:#f7f9ff}}
.open-entry:hover{{background:#e8efff;text-decoration:underline}}
.detail{{white-space:pre-wrap}}
.hidden{{display:none!important}}
@media(max-width:900px){{.filters{{grid-template-columns:1fr 1fr}}}}
</style></head><body>
<div class="toolbar">
  <h1>PO Validation Report</h1>
  <div class="summary">{summary_cards}</div>
  <div class="filters">
    <select id="levelFilter">{''.join(level_options)}</select>
    <select id="checkFilter">{''.join(check_options)}</select>
    <input id="fileFilter" placeholder="Filter file name/path">
    <input id="contextFilter" placeholder="Filter msgctxt/context">
    <input id="textFilter" placeholder="Filter detail/text">
    <label><input id="showOk" type="checkbox" checked> Show OK files</label>
    <button id="clearFilters" type="button">Clear filters</button>
  </div>
  <div id="visibleCount"></div>
</div>
<main id="report">{''.join(rows)}</main>
<script>
const levelFilter = document.getElementById('levelFilter');
const checkFilter = document.getElementById('checkFilter');
const fileFilter = document.getElementById('fileFilter');
const contextFilter = document.getElementById('contextFilter');
const textFilter = document.getElementById('textFilter');
const showOk = document.getElementById('showOk');
const visibleCount = document.getElementById('visibleCount');
const clearFilters = document.getElementById('clearFilters');
const files = Array.from(document.querySelectorAll('.file'));
function norm(v) {{ return String(v || '').toLowerCase(); }}
function applyFilters() {{
  const level = levelFilter.value;
  const check = checkFilter.value;
  const fileNeedle = norm(fileFilter.value);
  const ctxNeedle = norm(contextFilter.value);
  const textNeedle = norm(textFilter.value);
  let visibleFiles = 0;
  let visibleIssues = 0;
  for (const file of files) {{
    const fileText = norm(file.dataset.file);
    const fileMatches = !fileNeedle || fileText.includes(fileNeedle);
    let anyVisibleIssue = false;
    const isOkFile = Number(file.dataset.issueCount || 0) === 0;
    for (const issue of Array.from(file.querySelectorAll('.issue'))) {{
      const issueLevel = issue.dataset.level || '';
      const issueCheck = issue.dataset.check || '';
      const issueContext = norm(issue.dataset.context);
      const issueDetail = norm(issue.dataset.detail);
      const passLevel = !level || issueLevel === level;
      const passCheck = !check || issueCheck === check;
      const passContext = !ctxNeedle || issueContext.includes(ctxNeedle);
      const passText = !textNeedle || issueDetail.includes(textNeedle);
      const passOk = issueLevel !== 'OK' || showOk.checked;
      const visible = fileMatches && passLevel && passCheck && passContext && passText && passOk;
      issue.classList.toggle('hidden', !visible);
      if (visible && issueLevel !== 'OK') {{ visibleIssues += 1; }}
      if (visible) {{ anyVisibleIssue = true; }}
    }}
    const showFile = fileMatches && anyVisibleIssue && (!isOkFile || showOk.checked);
    file.classList.toggle('hidden', !showFile);
    if (showFile) {{ visibleFiles += 1; }}
  }}
  visibleCount.textContent = `Showing ${{visibleFiles}} file(s), ${{visibleIssues}} issue(s)`;
}}
[levelFilter, checkFilter, fileFilter, contextFilter, textFilter, showOk].forEach(el => el.addEventListener('input', applyFilters));
clearFilters.addEventListener('click', () => {{
  levelFilter.value = ''; checkFilter.value = ''; fileFilter.value = ''; contextFilter.value = ''; textFilter.value = ''; showOk.checked = true; applyFilters();
}});
document.querySelectorAll('[data-set-level]').forEach(btn => btn.addEventListener('click', () => {{ levelFilter.value = btn.dataset.setLevel || ''; applyFilters(); }}));
document.querySelectorAll('[data-clear-filters]').forEach(btn => btn.addEventListener('click', () => {{ clearFilters.click(); }}));
applyFilters();
</script>
</body></html>"""
