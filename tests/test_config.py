from pathlib import Path

from tasky.config import Config, now_iso


def test_from_env_defaults(tmp_path):
    env = {
        "TASKY_HOME": str(tmp_path / "home"),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"),
    }
    config = Config.from_env(env)
    assert config.home == tmp_path / "home"
    assert config.db_path == tmp_path / "home" / "tasky.db"
    assert config.log_dir == tmp_path / "home" / "logs"
    assert config.claude_config_dir == tmp_path / "claude"
    assert config.port == 7733
    assert config.queue_prefix == "++"
    assert config.max_chain == 5
    assert config.max_result == 8000
    assert config.context_items == 10
    assert config.claude_bin == "claude"


def test_from_env_overrides(tmp_path):
    env = {
        "TASKY_HOME": str(tmp_path / "home"),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"),
        "TASKY_PORT": "9001",
        "TASKY_QUEUE_PREFIX": "::",
        "TASKY_MAX_CHAIN": "2",
        "TASKY_MAX_RESULT": "100",
        "TASKY_CONTEXT_ITEMS": "3",
        "TASKY_CLAUDE_BIN": "/usr/bin/claude",
    }
    config = Config.from_env(env)
    assert config.port == 9001
    assert config.queue_prefix == "::"
    assert config.max_chain == 2
    assert config.max_result == 100
    assert config.context_items == 3
    assert config.claude_bin == "/usr/bin/claude"


def test_from_env_invalid_int_falls_back_to_default(tmp_path):
    env = {
        "TASKY_HOME": str(tmp_path / "home"),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"),
        "TASKY_PORT": "not-a-number",
        "TASKY_MAX_CHAIN": "3.5",
    }
    config = Config.from_env(env)
    assert config.port == 7733
    assert config.max_chain == 5


def test_from_env_home_falls_back_to_xdg_data_home(tmp_path):
    env = {
        "XDG_DATA_HOME": str(tmp_path / "xdg"),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"),
    }
    config = Config.from_env(env)
    assert config.home == tmp_path / "xdg" / "tasky"


def test_from_env_home_falls_back_to_local_share(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude")}
    config = Config.from_env(env)
    assert config.home == tmp_path / ".local" / "share" / "tasky"


def test_from_env_claude_config_dir_falls_back_to_dot_claude(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    env = {"TASKY_HOME": str(tmp_path / "home")}
    config = Config.from_env(env)
    assert config.claude_config_dir == tmp_path / ".claude"


def test_from_env_defaults_to_os_environ(monkeypatch, tmp_path):
    monkeypatch.setenv("TASKY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    config = Config.from_env()
    assert config.home == tmp_path / "home"


def test_now_iso_format():
    value = now_iso()
    assert value.endswith("Z")
    date_part, time_part = value[:-1].split("T")
    assert len(date_part) == 10
    fractional = time_part.split(".")[1]
    assert len(fractional) == 3
    assert fractional.isdigit()
