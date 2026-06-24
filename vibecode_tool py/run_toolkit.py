from pathlib import Path
import os
import sys
import traceback

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if SRC.exists():
    sys.path.insert(0, str(SRC))


def _show_startup_error(message: str) -> None:
    log_path = ROOT / "toolkit_launch_error.log"
    try:
        log_path.write_text(message, encoding="utf-8")
    except Exception:
        pass

    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None,
                f"DR PO Toolkit cannot start.\n\n{message[:1800]}\n\nLog:\n{log_path}",
                "DR PO Toolkit startup error",
                0x10,
            )
            return
        except Exception:
            pass

    print(message, file=sys.stderr)


try:
    from dr_po_toolkit.gui import main
except Exception:
    _show_startup_error(traceback.format_exc())
    raise


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _show_startup_error(traceback.format_exc())
        raise
