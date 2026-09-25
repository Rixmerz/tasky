"""Runtime configuration for Tasky, resolved from environment variables."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _int_env(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _str_env(env: Mapping[str, str], key: str, default: str) -> str:
    return env.get(key) or default


@dataclass(frozen=True)
class Config:
    home: Path
    db_path: Path
    log_dir: Path
    claude_config_dir: Path
    port: int
    queue_prefix: str
    max_chain: int
    max_result: int
    context_items: int
    claude_bin: str
    allow_bypass: bool
    token_path: Path
    history_model: str = "sonnet"
    history_max_batches: int = 8
    dead_end_items: int = 5
    card_language: str = ""
    card_translate: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        env = os.environ if env is None else env

        home_raw = env.get("TASKY_HOME")
        if home_raw:
            home = Path(home_raw).expanduser().absolute()
        else:
            xdg_raw = env.get("XDG_DATA_HOME")
            data_home = Path(xdg_raw) if xdg_raw else Path.home() / ".local" / "share"
            home = data_home / "tasky"

        claude_config_raw = env.get("CLAUDE_CONFIG_DIR")
        claude_config_dir = (
            Path(claude_config_raw) if claude_config_raw else Path.home() / ".claude"
        )

        return cls(
            home=home,
            db_path=home / "tasky.db",
            log_dir=home / "logs",
            claude_config_dir=claude_config_dir,
            port=_int_env(env, "TASKY_PORT", 7733),
            queue_prefix=_str_env(env, "TASKY_QUEUE_PREFIX", "++"),
            max_chain=_int_env(env, "TASKY_MAX_CHAIN", 5),
            max_result=_int_env(env, "TASKY_MAX_RESULT", 8000),
            context_items=_int_env(env, "TASKY_CONTEXT_ITEMS", 10),
            claude_bin=_str_env(env, "TASKY_CLAUDE_BIN", "claude"),
            allow_bypass=env.get("TASKY_ALLOW_BYPASS", "").strip().lower() in ("1", "true", "yes"),
            token_path=home / "token",
            history_model=_str_env(env, "TASKY_HISTORY_MODEL", "sonnet"),
            history_max_batches=max(1, _int_env(env, "TASKY_HISTORY_MAX_BATCHES", 8)),
            dead_end_items=max(0, _int_env(env, "TASKY_DEAD_END_ITEMS", 5)),
            card_language=_str_env(env, "TASKY_LANGUAGE", ""),
            card_translate=env.get("TASKY_CARDS_TRANSLATE", "").strip().lower()
            not in ("0", "false", "no"),
        )


def now_iso() -> str:
    now = datetime.now(timezone.utc)
    millis = now.microsecond // 1000
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{millis:03d}Z"


def token_proof(token: str, nonce: str, port: int) -> str:
    """HMAC that shows possession of the token without revealing it.

    Bound to the port so a proof captured from one listener cannot be replayed
    as another's.
    """
    message = f"{nonce}:{port}".encode("ascii")
    return hmac.new(token.encode("ascii"), message, hashlib.sha256).hexdigest()


_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


def load_token(config: Config, *, create: bool = True) -> str | None:
    """Return the dashboard API token, creating it (mode 0600) when missing.

    The token is what separates "a process running as this user that can read
    the data directory" from "anything that can reach the loopback port", such
    as another local account or a sandboxed app sharing the network namespace.
    """
    path = config.token_path
    exists = True
    try:
        token = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        token, exists = "", False
    except (OSError, UnicodeDecodeError):
        token = ""
    if _TOKEN_RE.match(token):
        return token
    if not create:
        return None
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    token = secrets.token_urlsafe(32)
    tmp = path.with_name(f".token.{os.getpid()}.{secrets.token_hex(8)}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(token)
    try:
        if exists:
            # An unreadable or malformed token is replaced, not trusted.
            os.replace(tmp, path)
        else:
            # link() fails if another process created the token first; theirs wins.
            os.link(tmp, path)
    except FileExistsError:
        return load_token(config, create=False)
    finally:
        tmp.unlink(missing_ok=True)
    return token
