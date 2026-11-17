import os
import subprocess
import sys

import pytest

from meet_standin.meet import chrome


def make_lock(profile, target):
    profile.mkdir(exist_ok=True)
    os.symlink(target, profile / "SingletonLock")


def dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_no_lock_means_free(tmp_path):
    assert chrome.profile_in_use(tmp_path) is None
    chrome.check_profile_free(tmp_path)


def test_lock_held_by_live_process(tmp_path):
    make_lock(tmp_path / "p", f"my-laptop-{os.getpid()}")
    assert chrome.profile_in_use(tmp_path / "p") == os.getpid()
    with pytest.raises(chrome.ChromeError, match="already using the profile"):
        chrome.check_profile_free(tmp_path / "p")


def test_hostname_with_dashes(tmp_path):
    make_lock(tmp_path / "p", f"home-laptop-01-{os.getpid()}")
    assert chrome.profile_in_use(tmp_path / "p") == os.getpid()


def test_stale_lock_is_free(tmp_path):
    make_lock(tmp_path / "p", f"my-laptop-{dead_pid()}")
    assert chrome.profile_in_use(tmp_path / "p") is None


def test_unparseable_lock_is_free(tmp_path):
    make_lock(tmp_path / "p", "garbage")
    assert chrome.profile_in_use(tmp_path / "p") is None


def test_find_chrome_missing(monkeypatch):
    monkeypatch.setattr(chrome.sys, "platform", "linux")
    monkeypatch.setattr(chrome.shutil, "which", lambda name: None)
    with pytest.raises(chrome.ChromeError, match="not installed"):
        chrome.find_chrome()


def test_find_chrome_linux(monkeypatch):
    monkeypatch.setattr(chrome.sys, "platform", "linux")
    stable = "/usr/bin/google-chrome-stable"
    monkeypatch.setattr(chrome.shutil, "which", lambda name: stable if "stable" in name else None)
    assert str(chrome.find_chrome()) == stable
