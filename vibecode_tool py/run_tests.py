import importlib.util
import inspect
import pathlib
import sys
import traceback

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

failed = 0
for path in sorted((ROOT / "tests").glob("test_*.py")):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    for name, fn in inspect.getmembers(module, inspect.isfunction):
        if not name.startswith("test_"):
            continue
        try:
            fn()
            print(f"PASS {path.name}::{name}")
        except Exception:
            failed += 1
            print(f"FAIL {path.name}::{name}")
            traceback.print_exc()
if failed:
    raise SystemExit(1)
print("All tests passed.")
