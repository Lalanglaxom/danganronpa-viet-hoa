from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

DANGANVIETHOA_REPOSITORY_URL = "https://github.com/Lalanglaxom/danganronpa-viet-hoa.git"


def validate_repository_folder(folder: str | Path) -> Path:
    """Return a configured Git repository folder or raise a useful error."""
    raw = str(folder).strip()
    if not raw:
        raise ValueError("Set Settings > danganviethoa folder first.")

    repo = Path(raw).expanduser()
    if not repo.exists() or not repo.is_dir():
        raise ValueError(f"Folder not found:\n{repo}")
    if not (repo / ".git").exists():
        raise ValueError(
            "This folder is not a Git repository.\n\n"
            f"Clone {DANGANVIETHOA_REPOSITORY_URL} first, then select the cloned folder."
        )
    return repo


def build_pull_command() -> str:
    """Build the command shown in CMD for a safe, inspectable pull."""
    return "title Danganronpa Viet Hoa - Git Pull && git remote -v && git status --short --branch && git pull"


def create_commit_message_file(message: str) -> Path:
    """Store the commit message outside the shell to avoid command injection."""
    cleaned = str(message).strip()
    if not cleaned:
        raise ValueError("Commit message cannot be empty.")

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".txt",
        prefix="dr_po_toolkit_commit_",
        delete=False,
    ) as handle:
        handle.write(cleaned)
        handle.write("\n")
        return Path(handle.name)


def build_push_command(commit_message_file: str | Path) -> str:
    """Build add/inspect/commit/push commands for a visible CMD window."""
    message_path = Path(commit_message_file)
    commit_cmd = subprocess.list2cmdline(["git", "commit", "-F", str(message_path)])
    delete_cmd = subprocess.list2cmdline(["del", "/Q", str(message_path)])
    return (
        "title Danganronpa Viet Hoa - Git Push"
        " && git remote -v"
        " && git status --short --branch"
        " && git add -A"
        " && git diff --cached --stat"
        f" && {commit_cmd}"
        f" && {delete_cmd}"
        " && git push"
    )


def launch_windows_cmd(repository_folder: str | Path, command: str) -> None:
    """Open a new Windows Command Prompt in the repository and run a command."""
    repo = validate_repository_folder(repository_folder)
    if os.name != "nt":
        raise RuntimeError("Git Pull/Push CMD buttons are available on Windows only.")

    creation_flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    subprocess.Popen(
        ["cmd.exe", "/K", command],
        cwd=str(repo),
        creationflags=creation_flags,
    )
