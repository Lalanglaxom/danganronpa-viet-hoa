from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

try:
    import pytest
except ImportError as exc:  # pragma: no cover - developer convenience
    raise SystemExit("pytest is required to run the test suite: python -m pip install pytest") from exc

raise SystemExit(pytest.main(["-q", str(ROOT / "tests")]))
