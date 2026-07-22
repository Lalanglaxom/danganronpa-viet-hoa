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
    """Build a pull command that preserves local edits and avoids merge commits."""
    return (
        "title Danganronpa Viet Hoa - Git Pull"
        " && git remote -v"
        " && git status --short --branch"
        " && git fetch --prune origin"
        " && git pull --rebase --autostash"
    )


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


def create_push_script(commit_message_file: str | Path) -> Path:
    """Create a visible Windows batch script for staging, committing, and pushing."""
    message_path = Path(commit_message_file).resolve()
    quoted_message = subprocess.list2cmdline([str(message_path)])

    script = f'''@echo off
setlocal EnableExtensions EnableDelayedExpansion
set "FINAL_EXIT=0"
title Danganronpa Viet Hoa - Git Push

echo [1/4] Scanning files...
echo       git add .
git add .
if errorlevel 1 (
    set "GIT_EXIT=!ERRORLEVEL!"
    goto :failed_with_code
)

echo.
echo [2/4] Checking staged changes...
echo       git diff --cached --quiet
git diff --cached --quiet
set "DIFF_EXIT=!ERRORLEVEL!"
if "!DIFF_EXIT!"=="0" (
    echo No new staged changes. Existing local commits will still be pushed.
) else if "!DIFF_EXIT!"=="1" (
    echo.
    echo [3/4] Creating commit...
    echo       git commit --quiet -F ^<message file^>
    git commit --quiet -F {quoted_message}
    if errorlevel 1 (
        set "GIT_EXIT=!ERRORLEVEL!"
        goto :failed_with_code
    )
) else (
    set "GIT_EXIT=!DIFF_EXIT!"
    goto :failed_with_code
)

if "!DIFF_EXIT!"=="0" (
    echo.
    echo [3/4] No commit needed.
)

del /Q {quoted_message} >nul 2>&1

echo.
echo [4/4] Uploading to remote...
echo       git push origin main
git push origin main
if errorlevel 1 (
    set "GIT_EXIT=!ERRORLEVEL!"
    goto :failed_with_code
)

echo.
echo Push completed successfully.
goto :done

:failed_with_code
set "FINAL_EXIT=!GIT_EXIT!"
del /Q {quoted_message} >nul 2>&1
echo.
echo Git stopped with error code !GIT_EXIT!. Review the output above.

:done
echo.
echo This window will stay open for inspection.
del /Q "%~f0" >nul 2>&1
endlocal & exit /b %FINAL_EXIT%
'''

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\r\n",
        suffix=".cmd",
        prefix="dr_po_toolkit_push_",
        delete=False,
    ) as handle:
        handle.write(script)
        return Path(handle.name)


def build_push_command(push_script_file: str | Path) -> str:
    """Build the command used to run the generated push batch file."""
    script_path = Path(push_script_file).resolve()
    return f"call {subprocess.list2cmdline([str(script_path)])}"


def launch_windows_cmd(repository_folder: str | Path, command: str) -> None:
    """Open a persistent Windows Command Prompt and keep all Git output visible."""
    repo = validate_repository_folder(repository_folder)
    if os.name != "nt":
        raise RuntimeError("Git Pull/Push CMD buttons are available on Windows only.")

    # /K keeps the prompt open. The explicit pause also protects against terminal
    # profiles that would otherwise close a completed child command immediately.
    # Delayed expansion is required so ERRORLEVEL is read after Git finishes.
    persistent_command = (
        f"{command}"
        ' & set "DR_GIT_EXIT=!ERRORLEVEL!"'
        " & echo."
        ' & if not "!DR_GIT_EXIT!"=="0" ('
        "echo Git command finished with error code !DR_GIT_EXIT!."
        " ) else (echo Git command finished successfully.)"
        " & echo Review the output above. This window will not close automatically."
        " & echo Press any key to continue to the command prompt..."
        " & pause >nul"
    )

    creation_flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    subprocess.Popen(
        ["cmd.exe", "/D", "/V:ON", "/K", persistent_command],
        cwd=str(repo),
        creationflags=creation_flags,
    )
