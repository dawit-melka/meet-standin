import os
import subprocess
import sys


def run_main(cwd, extra_env=None):
    ours = {"TELEGRAM_BOT_TOKEN", "ALLOWED_CHAT_ID", "DATA_DIR", "PROFILE_DIR"}
    env = {k: v for k, v in os.environ.items() if k not in ours}
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-m", "meet_standin"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_exits_with_clear_message_without_env(tmp_path):
    result = run_main(tmp_path)
    assert result.returncode == 2
    assert "TELEGRAM_BOT_TOKEN is not set" in result.stderr
    assert ".env.example" in result.stderr


def test_reads_env_file_from_cwd(tmp_path):
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=123:abc\nALLOWED_CHAT_ID=42\n")
    result = run_main(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "data" / "profile").is_dir()
