from __future__ import annotations

import os
import struct
from pathlib import Path
from unittest.mock import patch

from dr_po_toolkit.drat_repack import (
    deploy_filename_plans,
    plan_files_by_filename,
    repack_all_formats,
    repack_all_wads,
    repack_pak_folder,
    repack_text_folder,
    repack_wad_folder,
    resolve_drat_workspace,
)


def _write_po(path: Path, msgid: str = "Hello", msgstr: str = "Xin chào") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'msgctxt "0001"\nmsgid "{msgid}"\nmsgstr "{msgstr}"\n',
        encoding="utf-8",
    )


def _read_simple_text_pak(path: Path) -> list[str]:
    data = path.read_bytes()
    count = struct.unpack_from("<I", data, 0)[0]
    offsets = struct.unpack_from(f"<{count + 1}I", data, 4)
    values: list[str] = []
    for index in range(count):
        chunk = data[offsets[index] : offsets[index + 1]]
        if chunk.startswith(b"\xff\xfe"):
            chunk = chunk[2:]
        while chunk.endswith(b"\x00\x00"):
            chunk = chunk[:-2]
        values.append(chunk.decode("utf-16le"))
    return values


def _read_generic_pak(path: Path) -> list[bytes]:
    data = path.read_bytes()
    count = struct.unpack_from("<I", data, 0)[0]
    offsets = struct.unpack_from(f"<{count}I", data, 4)
    return [
        data[offsets[index] : offsets[index + 1] if index + 1 < count else len(data)].rstrip(b"\x00")
        for index in range(count)
    ]


def _extract_wad(path: Path) -> dict[str, bytes]:
    data = path.read_bytes()
    magic, major, minor, unknown = struct.unpack_from("<IIII", data, 0)
    assert (magic, major, minor, unknown) == (0x52414741, 1, 1, 0)
    cursor = 16
    count = struct.unpack_from("<I", data, cursor)[0]
    cursor += 4
    entries: list[tuple[str, int, int]] = []
    for _ in range(count):
        name_len = struct.unpack_from("<I", data, cursor)[0]
        cursor += 4
        name = data[cursor : cursor + name_len].decode("utf-8")
        cursor += name_len
        size, _zero1, offset, _zero2 = struct.unpack_from("<IIII", data, cursor)
        cursor += 16
        entries.append((name, size, offset))

    dir_count = struct.unpack_from("<I", data, cursor)[0]
    cursor += 4
    for _ in range(dir_count):
        name_len = struct.unpack_from("<I", data, cursor)[0]
        cursor += 4 + name_len
        child_count = struct.unpack_from("<I", data, cursor)[0]
        cursor += 4
        for _child in range(child_count):
            child_len = struct.unpack_from("<I", data, cursor)[0]
            cursor += 4 + child_len + 1

    body_start = cursor
    return {name: data[body_start + offset : body_start + offset + size] for name, size, offset in entries}


def test_resolve_drat_workspace_accepts_parent_or_manual_root(tmp_path: Path):
    manual = tmp_path / "DRAT" / "DR1 (PC) [MANUAL MODE]"
    (manual / "EXTRACTED").mkdir(parents=True)

    from_parent = resolve_drat_workspace(tmp_path / "DRAT")
    from_manual = resolve_drat_workspace(manual)

    assert from_parent.manual_root == manual
    assert from_manual.manual_root == manual
    assert from_parent.profile.is_dr1
    assert from_parent.repacked_root == manual / "REPACKED"


def test_repack_text_folder_builds_simple_text_pak_and_lin(tmp_path: Path):
    manual = tmp_path / "DR1 (PC) [MANUAL MODE]"
    (manual / "EXTRACTED").mkdir(parents=True)
    workspace = resolve_drat_workspace(manual)

    simple = tmp_path / "simple"
    _write_po(simple / "simple.po")
    simple_output = repack_text_folder(simple, tmp_path / "out", profile=workspace.profile)
    assert simple_output.suffix == ".pak"
    assert _read_simple_text_pak(simple_output) == ["Xin chào"]

    lin = tmp_path / "scene"
    _write_po(lin / "scene.po", msgstr="Bản dịch")
    (lin / "scene.bytecode").write_bytes(b"\x10\x20\x30")
    lin_output = repack_text_folder(lin, tmp_path / "out", profile=workspace.profile)
    data = lin_output.read_bytes()
    n_parts, header_size, text_offset, file_size = struct.unpack_from("<IIII", data, 0)
    assert (n_parts, header_size, text_offset, file_size) == (2, 0x10, 0x13, len(data))
    assert data[0x10:0x13] == b"\x10\x20\x30"
    assert struct.unpack_from("<I", data, text_offset)[0] == 1


def test_repack_generic_pak_uses_natural_order_and_alignment(tmp_path: Path):
    source = tmp_path / "container"
    source.mkdir()
    (source / "file-[0010].bin").write_bytes(b"ten")
    (source / "file-[0002].bin").write_bytes(b"two")

    output = repack_pak_folder(source, tmp_path / "out")
    contents = _read_generic_pak(output)

    assert contents[0] == b"two"
    assert contents[1] == b"ten"
    data = output.read_bytes()
    first_offset, second_offset = struct.unpack_from("<II", data, 4)
    assert first_offset % 0x10 == 0
    assert second_offset % 0x10 == 0


def test_repack_wad_roundtrip_preserves_paths_and_bodies(tmp_path: Path):
    source = tmp_path / "game_data"
    (source / "Dr1" / "data" / "script").mkdir(parents=True)
    (source / "root.bin").write_bytes(b"root")
    (source / "Dr1" / "data" / "script" / "scene.lin").write_bytes(b"lin")

    output = repack_wad_folder(source, tmp_path / "out")
    extracted = _extract_wad(output)

    assert extracted == {
        "root.bin": b"root",
        "Dr1/data/script/scene.lin": b"lin",
    }


def test_full_drat_repack_pipeline_formats_script_wad_and_game(tmp_path: Path):
    manual = tmp_path / "DRAT" / "DR1 (PC) [MANUAL MODE]"
    extracted = manual / "EXTRACTED"

    lin = extracted / "LIN" / "scene"
    _write_po(lin / "scene.po", msgstr="Cảnh")
    (lin / "scene.bytecode").write_bytes(b"\x01\x02")

    type1 = extracted / "TEXT PAK TYPE 1" / "system"
    _write_po(type1 / "system.po", msgstr="Hệ thống")

    type2_child = extracted / "TEXT PAK TYPE 2" / "script_pak_e01" / "e01_001"
    _write_po(type2_child / "e01_001.po", msgstr="Chương một")
    (type2_child / "e01_001.bytecode").write_bytes(b"\x03")

    type3_child = extracted / "TEXT PAK TYPE 3" / "outer" / "inner"
    _write_po(type3_child / "inner.po", msgstr="Bên trong")

    wad_root = extracted / "WAD" / "game_data"
    script = wad_root / "Dr1" / "data" / "script"
    script.mkdir(parents=True)
    for filename in ("scene.lin", "system.pak", "script_pak_e01.pak", "outer.pak"):
        (script / filename).write_bytes(b"old")

    game_folder = tmp_path / "game"
    game_folder.mkdir()
    (game_folder / "game_data.wad").write_bytes(b"old wad")

    workspace = resolve_drat_workspace(tmp_path / "DRAT")
    formats = repack_all_formats(workspace)
    assert not formats.errors
    assert {path.name for path in formats.outputs} == {
        "scene.lin",
        "system.pak",
        "script_pak_e01.pak",
        "outer.pak",
    }

    script_plan = plan_files_by_filename(formats.outputs, script)
    assert not script_plan.errors
    assert all((script / filename).read_bytes() == b"old" for filename in (
        "scene.lin",
        "system.pak",
        "script_pak_e01.pak",
        "outer.pak",
    ))

    wads = repack_all_wads(
        workspace,
        file_overrides={target: source for source, target in script_plan.matches},
    )
    assert not wads.errors
    assert [path.name for path in wads.outputs] == ["game_data.wad"]
    assert all((script / filename).read_bytes() == b"old" for filename in (
        "scene.lin",
        "system.pak",
        "script_pak_e01.pak",
        "outer.pak",
    ))

    game_plan = plan_files_by_filename(wads.outputs, game_folder)
    deploy_result = deploy_filename_plans([script_plan, game_plan])
    assert not deploy_result.errors
    assert deploy_result.copied == 5

    game_contents = _extract_wad(game_folder / "game_data.wad")
    assert game_contents["Dr1/data/script/scene.lin"] != b"old"
    assert game_contents["Dr1/data/script/system.pak"] != b"old"


def test_repack_all_formats_skips_unchanged_and_rebuilds_only_changed_jobs(tmp_path: Path):
    manual = tmp_path / "DRAT" / "DR1 (PC) [MANUAL MODE]"
    extracted = manual / "EXTRACTED"
    first = extracted / "TEXT PAK TYPE 1" / "first"
    second = extracted / "TEXT PAK TYPE 1" / "second"
    _write_po(first / "first.po", msgstr="Một")
    _write_po(second / "second.po", msgstr="Hai")

    workspace = resolve_drat_workspace(manual)
    initial = repack_all_formats(workspace)
    assert not initial.errors
    assert {path.name for path in initial.built_outputs} == {"first.pak", "second.pak"}
    assert not initial.unchanged_outputs
    original_bytes = {path.name: path.read_bytes() for path in initial.outputs}
    original_mtimes = {path.name: path.stat().st_mtime_ns for path in initial.outputs}

    unchanged = repack_all_formats(workspace)
    assert not unchanged.errors
    assert not unchanged.built_outputs
    assert {path.name for path in unchanged.unchanged_outputs} == {"first.pak", "second.pak"}
    assert {path.name: path.stat().st_mtime_ns for path in unchanged.outputs} == original_mtimes

    _write_po(first / "first.po", msgstr="Một đã đổi")
    changed = repack_all_formats(workspace)
    assert not changed.errors
    assert [path.name for path in changed.built_outputs] == ["first.pak"]
    assert [path.name for path in changed.unchanged_outputs] == ["second.pak"]
    assert (workspace.repacked_root / "TEXT PAK TYPE 1" / "first.pak").read_bytes() != original_bytes["first.pak"]
    assert (workspace.repacked_root / "TEXT PAK TYPE 1" / "second.pak").read_bytes() == original_bytes["second.pak"]


def test_format_batch_does_not_commit_partial_outputs_on_error(tmp_path: Path):
    manual = tmp_path / "DR1 (PC) [MANUAL MODE]"
    extracted = manual / "EXTRACTED" / "TEXT PAK TYPE 1"
    valid = extracted / "valid"
    invalid = extracted / "invalid"
    _write_po(valid / "valid.po", msgstr="OK")
    invalid.mkdir(parents=True)

    workspace = resolve_drat_workspace(manual)
    result = repack_all_formats(workspace)

    assert result.errors
    assert not (workspace.repacked_root / "TEXT PAK TYPE 1" / "valid.pak").exists()


def test_wad_virtual_override_keeps_source_untouched_and_is_incremental(tmp_path: Path):
    manual = tmp_path / "DR1 (PC) [MANUAL MODE]"
    source_wad = manual / "EXTRACTED" / "WAD" / "game_data"
    target = source_wad / "Dr1" / "data" / "script" / "scene.lin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old")
    generated = tmp_path / "scene.lin"
    generated.write_bytes(b"new")

    workspace = resolve_drat_workspace(manual)
    initial = repack_all_wads(workspace, file_overrides={target: generated})
    assert not initial.errors
    assert [path.name for path in initial.built_outputs] == ["game_data.wad"]
    assert target.read_bytes() == b"old"
    assert _extract_wad(initial.outputs[0])["Dr1/data/script/scene.lin"] == b"new"

    unchanged = repack_all_wads(workspace, file_overrides={target: generated})
    assert not unchanged.errors
    assert not unchanged.built_outputs
    assert [path.name for path in unchanged.unchanged_outputs] == ["game_data.wad"]

    generated.write_bytes(b"newer")
    rebuilt = repack_all_wads(workspace, file_overrides={target: generated})
    assert not rebuilt.errors
    assert [path.name for path in rebuilt.built_outputs] == ["game_data.wad"]
    assert target.read_bytes() == b"old"
    assert _extract_wad(rebuilt.outputs[0])["Dr1/data/script/scene.lin"] == b"newer"


def test_multi_plan_deployment_validates_everything_before_copying(tmp_path: Path):
    first_source = tmp_path / "generated" / "first.bin"
    missing_source = tmp_path / "generated" / "missing.bin"
    first_source.parent.mkdir()
    first_source.write_bytes(b"new")
    missing_source.write_bytes(b"missing")

    first_target_root = tmp_path / "target1"
    second_target_root = tmp_path / "target2"
    first_target_root.mkdir()
    second_target_root.mkdir()
    first_target = first_target_root / "first.bin"
    first_target.write_bytes(b"old")

    valid_plan = plan_files_by_filename([first_source], first_target_root)
    invalid_plan = plan_files_by_filename([missing_source], second_target_root)
    result = deploy_filename_plans([valid_plan, invalid_plan])

    assert result.errors
    assert result.copied == 0
    assert first_target.read_bytes() == b"old"


def test_transactional_deployment_rolls_back_when_a_replace_fails(tmp_path: Path):
    generated = tmp_path / "generated"
    target_root = tmp_path / "target"
    generated.mkdir()
    target_root.mkdir()
    first_source = generated / "first.bin"
    second_source = generated / "second.bin"
    first_target = target_root / "first.bin"
    second_target = target_root / "second.bin"
    first_source.write_bytes(b"new first")
    second_source.write_bytes(b"new second")
    first_target.write_bytes(b"old first")
    second_target.write_bytes(b"old second")
    plan = plan_files_by_filename([first_source, second_source], target_root)

    real_replace = os.replace

    def fail_second_staged_replace(source, target):
        if Path(target) == second_target and ".drat-stage-" in Path(source).name:
            raise OSError("simulated replace failure")
        return real_replace(source, target)

    with patch("dr_po_toolkit.drat_repack.os.replace", side_effect=fail_second_staged_replace):
        result = deploy_filename_plans([plan])

    assert result.errors
    assert result.copied == 0
    assert first_target.read_bytes() == b"old first"
    assert second_target.read_bytes() == b"old second"


def test_config_adds_drat_folder_and_migrates_old_repack_selection(tmp_path: Path):
    import json

    from dr_po_toolkit.config import DEFAULT_CONFIG, load_config, save_config

    assert "drat_folder_path" in DEFAULT_CONFIG
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "extracted_path": "D:/DRAT/DR1 (PC) [MANUAL MODE]/EXTRACTED",
                "backup_sync_dr_options": ["e01", "system"],
            }
        ),
        encoding="utf-8",
    )

    loaded = load_config(config_path)
    assert loaded["drat_folder_path"].replace("\\", "/").endswith("DR1 (PC) [MANUAL MODE]")
    assert loaded["repack_dr_options"] == ["e01", "system"]
    assert "backup_sync_dr_options" not in loaded

    save_config(loaded, config_path)
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert "backup_sync_dr_options" not in saved
    assert saved["repack_dr_options"] == ["e01", "system"]


def test_wad_repack_reports_live_copy_progress(tmp_path: Path):
    source = tmp_path / "game_data"
    source.mkdir()
    (source / "large.bin").write_bytes(b"0123456789abcdef")
    progress: list[tuple[int, int, str]] = []

    with patch.multiple(
        "dr_po_toolkit.drat_repack",
        _COPY_CHUNK_SIZE=4,
        _COPY_PROGRESS_STEP=4,
        _PROGRESS_UNIT_BYTES=1,
    ):
        repack_wad_folder(
            source,
            tmp_path / "out",
            progress=lambda done, total, path: progress.append((done, total, path.name)),
        )

    assert progress[0][0] == 0
    assert progress[-1][0] == progress[-1][1]
    assert any(0 < done < total for done, total, _name in progress)
