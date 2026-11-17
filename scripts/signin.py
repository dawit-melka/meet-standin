"""One-time Google sign-in for the bot's Chrome profile.

Opens plain Google Chrome (not under automation, which Google blocks for sign-in) on the bot's
profile, with the same cookie-encryption flags Playwright uses, so the session carries over.
Sign in, then close Chrome completely.

    uv run python scripts/signin.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

from meet_standin.config import Paths
from meet_standin.meet.chrome import (
    SIGNIN_KEYCHAIN_ARGS,
    ChromeError,
    check_profile_free,
    find_chrome,
)


def main() -> int:
    load_dotenv(Path.cwd() / ".env")
    paths = Paths.from_env()
    paths.ensure_dirs()
    try:
        chrome = find_chrome()
        check_profile_free(paths.profile_dir)
    except ChromeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"Profile: {paths.profile_dir}")
    print("Sign in to your Google account in the Chrome window, then quit Chrome completely.")
    print("Tip: open meet.google.com afterwards to accept any first-run prompts.")
    subprocess.run(
        [
            str(chrome),
            f"--user-data-dir={paths.profile_dir}",
            *SIGNIN_KEYCHAIN_ARGS,
            "--no-first-run",
            "--no-default-browser-check",
            "https://accounts.google.com/",
        ],
        check=False,
    )
    print("Chrome closed. Next: uv run python scripts/poc.py --signin-only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
