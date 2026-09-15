"""Runs the dashboard's JavaScript unit tests so the Python suite gates them too."""

import shutil
import subprocess
from pathlib import Path

import pytest

WEB_TESTS = Path(__file__).parent / "web"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_web_digest_suite_passes():
    files = sorted(str(p) for p in WEB_TESTS.glob("*.test.mjs"))
    if not files:
        pytest.skip("no JavaScript tests present")
    # Explicit files: some node releases reject a bare directory argument.
    result = subprocess.run(
        ["node", "--test", *files],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
