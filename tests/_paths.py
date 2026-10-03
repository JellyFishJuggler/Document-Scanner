"""Shared paths/config for the test scripts.

Nothing secret lives here. The API key below is the throwaway value used only for
the local test container; override with SCANLY_TEST_KEY if you run your own.

Artifacts (generated fixtures, before/after images) go to /tmp by default so they
can never be committed.
"""
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TESTS = REPO / "tests"

ARTIFACTS = Path(os.environ.get("SCANLY_ARTIFACTS", "/tmp/scanly_artifacts"))
FIX = ARTIFACTS / "fixtures"
API = os.environ.get("SCANLY_TEST_URL", "http://127.0.0.1:18000")
KEY = os.environ.get("SCANLY_TEST_KEY", "test-secret-abc123")

ARTIFACTS.mkdir(parents=True, exist_ok=True)
FIX.mkdir(parents=True, exist_ok=True)