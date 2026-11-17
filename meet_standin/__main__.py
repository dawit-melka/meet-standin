"""Entry point: `python -m meet_standin` or `meet-standin`."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from meet_standin.config import ConfigError, Settings, setup_logging

log = logging.getLogger("meet_standin")


def main() -> int:
    # .env is read from the directory the bot is started in; real environment variables win.
    load_dotenv(Path.cwd() / ".env")
    setup_logging()

    try:
        settings = Settings.from_env()
    except ConfigError as e:
        print(f"meet-standin: {e}", file=sys.stderr)
        return 2

    settings.paths.ensure_dirs()
    log.info("Configuration OK. Data dir: %s", settings.paths.data_dir)
    log.info("The Telegram bot is not implemented yet (issue #3).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
