"""Settings from the environment (.env) and persisted state (state.json)."""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ENV_HELP = "Copy .env.example to .env and fill it in."


class ConfigError(Exception):
    """The configuration is missing or invalid. The message is meant for the user."""


@dataclass(frozen=True)
class Paths:
    """Where runtime data lives. Needs no secrets, so scripts can use it too."""

    data_dir: Path
    profile_dir: Path

    @property
    def state_file(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def recordings_dir(self) -> Path:
        return self.data_dir / "recordings"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Paths:
        env = os.environ if environ is None else environ
        data_dir = Path(env.get("DATA_DIR") or "data").expanduser().resolve()
        profile = env.get("PROFILE_DIR")
        profile_dir = Path(profile).expanduser().resolve() if profile else data_dir / "profile"
        return cls(data_dir=data_dir, profile_dir=profile_dir)

    def ensure_dirs(self) -> None:
        """Create the data dirs. The profile holds a live Google session, so it is owner-only."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.profile_dir.chmod(0o700)


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    allowed_chat_id: int
    paths: Paths

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if environ is None else environ

        token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise ConfigError(f"TELEGRAM_BOT_TOKEN is not set. {ENV_HELP}")

        raw_chat_id = env.get("ALLOWED_CHAT_ID", "").strip()
        if not raw_chat_id:
            raise ConfigError(f"ALLOWED_CHAT_ID is not set. {ENV_HELP}")
        try:
            chat_id = int(raw_chat_id)
        except ValueError:
            raise ConfigError(f"ALLOWED_CHAT_ID must be a number, got {raw_chat_id!r}.") from None

        return cls(telegram_bot_token=token, allowed_chat_id=chat_id, paths=Paths.from_env(env))


def load_state(path: Path) -> dict[str, Any]:
    """Read state.json. A missing file is an empty state."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path} is not valid JSON ({e}). Fix or delete it.") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a JSON object.")
    return data


def save_state(path: Path, data: Mapping[str, Any]) -> None:
    """Write state.json atomically, so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def setup_logging(level: str | None = None) -> None:
    """Log to stdout; under systemd, journald adds its own timestamps."""
    name = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, name, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    # python-telegram-bot's HTTP client logs every long-poll request at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
