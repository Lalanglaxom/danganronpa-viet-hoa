from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .backup import make_backups, restore_working_po_from_copies, sync_by_filename_report
from .linewrap import wrap_path
from .rules import apply_rules_to_path, load_rules
from .search import search_path
from .gemini_web import DEFAULT_BATCH_RETRIES, DEFAULT_CDP_URL, DEFAULT_MAX_ENTRIES_PER_BATCH, open_chrome_debug, run_gemini_web_path
from .translator import apply_response_to_file, write_manual_jobs
from .validation import format_text_report, validate_path, write_reports


def cmd_validate(args: argparse.Namespace) -> int:
    results = validate_path(args.path)
    report = format_text_report(results, args.path)
    print(report)
    if args.reports:
        txt, html = write_reports(results, args.reports, args.path)
        print(f"Saved: {txt}")
        print(f"Saved: {html}")
    return 1 if any(i.level == "ERROR" for issues in results.values() for i in issues) else 0


def cmd_replace(args: argparse.Namespace) -> int:
    rules = load_rules(args.rules)
    if args.only:
        keep = set(args.only)
        rules = [r for r in rules if r.id in keep]
    changes = apply_rules_to_path(args.path, rules, dry_run=args.dry_run)
    print(f"Rules loaded: {len(rules)}")
    print(f"Changes: {len(changes)}")
    for change in changes[:200]:
        print(f"{change.file} | {change.msgctxt} | {change.rule_id} | {change.count}")
        if args.verbose:
            print(f"  - {change.before}")
            print(f"  + {change.after}")
    if len(changes) > 200:
        print(f"... {len(changes)-200} more")
    if args.dry_run:
        print("Dry-run only. No files changed.")
    return 0


def cmd_linewrap(args: argparse.Namespace) -> int:
    results = wrap_path(args.path, soft=args.soft, hard=args.hard, max_cuts=args.max_cuts, dry_run=args.dry_run)
    total = sum(results.values())
    for path, count in results.items():
        if count or args.verbose:
            print(f"{path}: {count}")
    print(f"Entries wrapped: {total}")
    if args.dry_run:
        print("Dry-run only. No files changed.")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    results = search_path(
        args.path,
        args.phrase,
        search_msgid=args.msgid,
        search_msgstr=args.msgstr,
        case_sensitive=args.case,
        whole_word=args.whole_word,
        speaker=getattr(args, "speaker", ""),
        raw=getattr(args, "raw", False),
    )
    for r in results:
        print(f"{r.file} | {r.msgctxt} | msgid={r.hit_msgid} msgstr={r.hit_msgstr}")
        print(f"  msgid : {r.msgid}")
        print(f"  msgstr: {r.msgstr}")
    print(f"Results: {len(results)}")
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    overwrite = bool(getattr(args, "overwrite", False)) and not bool(getattr(args, "no_overwrite", False))
    count = make_backups(args.path, overwrite=overwrite)
    print(f"Backups written: {count}")
    if not overwrite:
        print("Existing Copy.po files were not touched.")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    result = sync_by_filename_report(args.source, args.target)

    def print_paths(title: str, paths: list[Path]) -> None:
        if not paths:
            return
        print(f"{title}: {len(paths)}")
        for path in paths:
            print(f"  - {path}")

    def print_pairs(title: str, pairs: list[tuple[Path, Path]]) -> None:
        if not pairs:
            return
        print(f"{title}: {len(pairs)}")
        for src, target in pairs:
            print(f"  - {src} -> {target}")

    print(f"Source files scanned: {result.source_files}")
    print(f"Target files scanned: {result.target_files}")
    if result.duplicate_source_names:
        print(f"Duplicate source filenames skipped: {result.duplicate_source_names}")
    if result.skipped_identical:
        print(f"Identical files skipped: {result.skipped_identical}")
    if result.skipped_self:
        print(f"Self-copy skipped: {result.skipped_self}")
    print_pairs("Copied source -> target", result.copied_files)
    print_paths("Duplicate source files not pasted", result.duplicate_source_files)
    print_paths("Source files with no target filename match (not pasted)", result.source_without_target)
    print_paths("Target files with no source filename match (not found in source)", result.target_without_source)
    print(f"Files synced: {result.copied}")
    return 0


def cmd_restore_from_copy(args: argparse.Namespace) -> int:
    results = restore_working_po_from_copies(args.paths, dry_run=args.dry_run)
    ok = sum(1 for r in results if r.ok)
    failed = len(results) - ok
    for r in results:
        status = "OK" if r.ok else "ERR"
        print(f"{status} | {r.action} | {r.copy_po} -> {r.work_po}" + (f" | {r.error}" if r.error else ""))
    print(f"Restored working PO files: {ok}")
    if failed:
        print(f"Failed/skipped: {failed}")
    if args.dry_run:
        print("Dry-run only. No files changed.")
    return 1 if failed else 0


def cmd_make_jobs(args: argparse.Namespace) -> int:
    written = write_manual_jobs(args.path, args.out, batch_size=args.batch_size, max_files=args.max_files)
    print(f"Files written: {len(written)}")
    for p in written:
        print(p)
    return 0


def cmd_apply_response(args: argparse.Namespace) -> int:
    count, errors = apply_response_to_file(args.po, args.response, allow_partial=args.allow_partial)
    print(f"Translations applied: {count}")
    if errors:
        print("Validation errors:")
        for e in errors:
            print(f"  {e.uid} | {e.msgctxt} | {e.reason}")
    return 1 if errors and not args.allow_partial else 0


def cmd_open_chrome(args: argparse.Namespace) -> int:
    cmd = open_chrome_debug(cdp_url=args.cdp_url)
    print("Chrome opened with remote debugging.")
    print("Login to Gemini in that Chrome window, then run gemini-web.")
    print("Command:", " ".join(str(x) for x in cmd))
    return 0


def cmd_gemini_web(args: argparse.Namespace) -> int:
    result = run_gemini_web_path(
        args.path,
        max_files=args.max_files,
        max_lines_per_batch=args.max_lines,
        max_entries_per_batch=args.max_entries,
        wait_between_batches=args.wait,
        cdp_url=args.cdp_url,
        allow_invalid=args.allow_invalid,
        rename_duplicates=not args.skip_duplicate_rename,
        create_missing_backups=not args.no_backup,
        rename_folders=not args.no_folder_rename,
        response_timeout_seconds=args.timeout,
        retry_count=args.retries,
        log=print,
    )

    if not result.files:
        print("No untranslated PO files found.")
        return 0

    for item in result.files:
        print(f"{item.file} | missing={item.missing_before} | applied={item.translated} | errors={len(item.errors)}")
        if item.debug_log:
            print(f"  debug: {item.debug_log}")
        if item.backup_created:
            print("  backup: created Copy.po")
        if item.folder_renamed_to:
            print(f"  folder: {item.folder_renamed_from} -> {item.folder_renamed_to}")
        elif item.folder_rename_skipped_reason:
            print(f"  folder: skipped ({item.folder_rename_skipped_reason})")
        for err in item.errors[:50]:
            print(f"  error: {err.uid} | {err.msgctxt} | {err.reason}")
        if len(item.errors) > 50:
            print(f"  ... {len(item.errors) - 50} more errors")

    print(f"Total translated: {result.total_translated}")
    print(f"Total errors: {result.total_errors}")
    return 1 if result.total_errors and not args.allow_invalid else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dr-po", description="Danganronpa PO Toolkit refactored base")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="validate PO files against Copy.po backups")
    p.add_argument("path")
    p.add_argument("--reports", help="folder to save .log and .html reports")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("replace", help="apply replacement rules")
    p.add_argument("path")
    p.add_argument("--rules", default="rules/mass_replace_rules.json")
    p.add_argument("--only", nargs="*", help="only these rule ids")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_replace)

    p = sub.add_parser("linewrap", help="wrap msgstr lines")
    p.add_argument("path")
    p.add_argument("--soft", type=int, default=58)
    p.add_argument("--hard", type=int, default=64)
    p.add_argument("--max-cuts", type=int, default=2)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_linewrap)

    p = sub.add_parser("search", help="search PO files")
    p.add_argument("path")
    p.add_argument("phrase", nargs="?", default="")
    p.add_argument("--msgid", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--msgstr", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--case", action="store_true")
    p.add_argument("--whole-word", action="store_true")
    p.add_argument("--raw", action="store_true", help="match original text without stripping CLT tags or other formatting")
    p.add_argument("--speaker", default="", help="speaker/context filter; use '|' for OR and '&' for AND")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("backup", help="create missing Copy.po backups without touching existing backups")
    p.add_argument("path")
    p.add_argument("--overwrite", action="store_true", help="overwrite existing Copy.po backups")
    p.add_argument("--no-overwrite", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("sync", help="sync PO files from source folder to target folder by filename")
    p.add_argument("source")
    p.add_argument("target")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("restore-from-copy", help="replace working .po files with clean content copied from matching - Copy.po backups")
    p.add_argument("paths", nargs="+", help="one or more folders/files; folders are scanned recursively")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_restore_from_copy)

    p = sub.add_parser("make-jobs", help="write manual Gemini request/prompt files")
    p.add_argument("path")
    p.add_argument("out")
    p.add_argument("--batch-size", type=int, default=20)
    p.add_argument("--max-files", type=int)
    p.set_defaults(func=cmd_make_jobs)

    p = sub.add_parser("apply-response", help="apply Gemini JSON response to a PO file")
    p.add_argument("po")
    p.add_argument("response")
    p.add_argument("--allow-partial", action="store_true")
    p.set_defaults(func=cmd_apply_response)

    p = sub.add_parser("open-chrome", help="open Chrome with remote debugging for Gemini Web")
    p.add_argument("--cdp-url", default=DEFAULT_CDP_URL, help="Chrome CDP URL")
    p.set_defaults(func=cmd_open_chrome)

    p = sub.add_parser("gemini-web", help="automatic Gemini Web translation through Chrome remote debugging")
    p.add_argument("path")
    p.add_argument("--max-files", type=int, default=59)
    p.add_argument("--max-lines", type=int, default=600, help="approx max PO lines per Gemini batch")
    p.add_argument("--max-entries", type=int, default=DEFAULT_MAX_ENTRIES_PER_BATCH, help="max entries per Gemini batch; smaller batches prevent Gemini Web hangs")
    p.add_argument("--wait", type=float, default=2.5, help="seconds after saving a batch before the next Gemini batch; minimum 2.5")
    p.add_argument("--timeout", type=int, default=180, help="seconds to wait for each Gemini response")
    p.add_argument("--retries", type=int, default=DEFAULT_BATCH_RETRIES, help="retry a stuck/invalid Gemini batch this many times")
    p.add_argument("--cdp-url", default=DEFAULT_CDP_URL, help="Chrome CDP URL")
    p.add_argument("--allow-invalid", action="store_true", help="write translations even if tag/placeholder validation fails")
    p.add_argument("--skip-duplicate-rename", action="store_true", help="do not remove duplicate suffixes like ' (1)'")
    p.add_argument("--no-backup", action="store_true", help="do not create missing Copy.po backups; existing Copy.po files are never overwritten")
    p.add_argument("--no-folder-rename", action="store_true", help="do not ask Gemini for folder summary rename")
    p.set_defaults(func=cmd_gemini_web)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
