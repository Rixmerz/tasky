import sys
from pathlib import Path

# Console-script pytest does not add the repo root to sys.path (no tasky
# install, no tests/__init__.py to anchor rootdir insertion at the repo top).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from tasky.config import Config  # noqa: E402
from tasky.store import Store  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_children(tmp_path, monkeypatch):
    # Workers and supervisors inherit os.environ, not the test's env dict: without
    # this a test that kicks the run queue launches the real `claude` and writes
    # to the real ledger.
    monkeypatch.setenv("TASKY_HOME", str(tmp_path / "tasky-home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setenv("TASKY_CLAUDE_BIN", "/usr/bin/false")


@pytest.fixture
def config(tmp_path):
    env = {
        "TASKY_HOME": str(tmp_path / "tasky-home"),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude-config"),
    }
    return Config.from_env(env)


@pytest.fixture
def store(config):
    with Store.open(config) as db:
        yield db
