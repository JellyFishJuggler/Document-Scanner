"""Shared paths/config for the test scripts.

Nothing secret lives here. The API key below is the throwaway value used only for
the local test container; override with SCANLY_TEST_KEY if you run your own.

Artifacts (generated fixtures, before/after images) go to /tmp by default so they
can never be committed.
"""
import os
from pathlib import Path

import cv2

REPO = Path(__file__).resolve().parents[1]
TESTS = REPO / "tests"

ARTIFACTS = Path(os.environ.get("SCANLY_ARTIFACTS", "/tmp/scanly_artifacts"))
FIX = ARTIFACTS / "fixtures"
API = os.environ.get("SCANLY_TEST_URL", "http://127.0.0.1:18000")
KEY = os.environ.get("SCANLY_TEST_KEY", "test-secret-abc123")

ARTIFACTS.mkdir(parents=True, exist_ok=True)
FIX.mkdir(parents=True, exist_ok=True)


def load_api_functions(*names):
    """Exec selected top-level defs/constants out of api.py and return them.

    The local .venv deliberately has no fastapi (the HTTP suite talks to the
    container over the network instead), so `import api` fails here. Rather than
    installing fastapi just to unit-test one pure function, this lifts the exact
    source out of api.py with ast and runs it. The code under test is still the
    real file, not a copy.
    """
    import ast

    # Pull in the module-level constants too, because the pure helpers reference
    # them (_jpeg_size reads _JPEG_STANDALONE, _JPEG_SOF_MARKERS, ...).
    wanted = set(names) | {
        "_JPEG_MAGIC", "_PNG_MAGIC", "_JPEG_STANDALONE", "_JPEG_SOF_MARKERS",
        "_ALLOWED_FORMATS",
    }
    tree = ast.parse((REPO / "api.py").read_text())
    ns = {"cv2": cv2}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in set(names):
            exec(compile(ast.Module(body=[node], type_ignores=[]), "api.py", "exec"), ns)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if {t.id for t in targets if isinstance(t, ast.Name)} & wanted:
                exec(compile(ast.Module(body=[node], type_ignores=[]), "api.py", "exec"), ns)
    missing = set(names) - set(ns)
    if missing:
        raise AssertionError(f"not found in api.py: {sorted(missing)}")
    return ns