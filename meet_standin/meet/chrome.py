"""Launching Google Chrome on the bot's profile, by hand (sign-in) or under Playwright.

The sign-in and the automated launches must use the same cookie-encryption flags.
Playwright passes ``--password-store=basic`` and ``--use-mock-keychain`` by default. A profile
signed in by a plain Chrome launch has cookies encrypted with the real OS keychain/libsecret key,
which Playwright's Chrome then can't decrypt, so the profile silently looks signed out. Signing in
with SIGNIN_KEYCHAIN_ARGS avoids that (issue #2).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# Must match the cookie-encryption flags Playwright adds by default.
SIGNIN_KEYCHAIN_ARGS = ("--password-store=basic", "--use-mock-keychain")

# Playwright defaults to drop: --enable-automation shows the "controlled by automated test
# software" bar and is a strong automation signal to Google.
PLAYWRIGHT_IGNORE_DEFAULT_ARGS = ["--enable-automation"]

MACOS_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
LINUX_CHROME_NAMES = ("google-chrome", "google-chrome-stable")


class ChromeError(Exception):
    """Chrome or its profile can't be used. The message is meant for the user."""


def find_chrome() -> Path:
    """Path to the installed Google Chrome (not Chromium, which Playwright's channel="chrome"
    wouldn't use)."""
    if sys.platform == "darwin":
        if MACOS_CHROME.exists():
            return MACOS_CHROME
    else:
        for name in LINUX_CHROME_NAMES:
            found = shutil.which(name)
            if found:
                return Path(found)
    raise ChromeError(
        "Google Chrome is not installed. On Ubuntu, install the .deb from google.com/chrome "
        "(not the Chromium snap), or run: uv run playwright install chrome"
    )


def profile_in_use(profile_dir: Path) -> int | None:
    """PID of a running Chrome using this profile, or None.

    Chrome marks a profile in use with a ``SingletonLock`` symlink whose target is
    ``<hostname>-<pid>``. A stale lock (process gone) counts as not in use.
    """
    lock = profile_dir / "SingletonLock"
    if not lock.is_symlink():
        return None
    target = os.readlink(lock)
    _, _, pid_text = target.rpartition("-")
    try:
        pid = int(pid_text)
    except ValueError:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid  # exists, owned by someone else
    return pid


def check_profile_free(profile_dir: Path) -> None:
    pid = profile_in_use(profile_dir)
    if pid is not None:
        raise ChromeError(
            f"Chrome (pid {pid}) is already using the profile {profile_dir}. "
            "Close that Chrome window first."
        )
