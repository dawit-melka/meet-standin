import json
import stat

import pytest

from meet_standin.config import ConfigError, Paths, Settings, load_state, save_state

VALID_ENV = {"TELEGRAM_BOT_TOKEN": "123:abc", "ALLOWED_CHAT_ID": "4242"}


def test_settings_from_env():
    s = Settings.from_env(VALID_ENV)
    assert s.telegram_bot_token == "123:abc"
    assert s.allowed_chat_id == 4242


def test_negative_chat_id_is_allowed():
    # Group chats have negative IDs.
    assert Settings.from_env({**VALID_ENV, "ALLOWED_CHAT_ID": "-100123"}).allowed_chat_id == -100123


@pytest.mark.parametrize("missing", ["TELEGRAM_BOT_TOKEN", "ALLOWED_CHAT_ID"])
def test_missing_setting_names_the_variable_and_points_to_env_example(missing):
    env = {k: v for k, v in VALID_ENV.items() if k != missing}
    with pytest.raises(ConfigError, match=rf"{missing} is not set.*\.env\.example"):
        Settings.from_env(env)


def test_non_numeric_chat_id():
    with pytest.raises(ConfigError, match="must be a number"):
        Settings.from_env({**VALID_ENV, "ALLOWED_CHAT_ID": "@me"})


def test_default_paths_are_under_cwd_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = Paths.from_env({})
    assert p.data_dir == tmp_path / "data"
    assert p.profile_dir == tmp_path / "data" / "profile"
    assert p.state_file == tmp_path / "data" / "state.json"
    assert p.recordings_dir == tmp_path / "data" / "recordings"


def test_custom_paths(tmp_path):
    p = Paths.from_env({"DATA_DIR": str(tmp_path / "d"), "PROFILE_DIR": str(tmp_path / "chrome")})
    assert p.data_dir == tmp_path / "d"
    assert p.profile_dir == tmp_path / "chrome"


def test_ensure_dirs_makes_profile_owner_only(tmp_path):
    p = Paths.from_env({"DATA_DIR": str(tmp_path / "data")})
    p.ensure_dirs()
    assert p.recordings_dir.is_dir()
    assert stat.S_IMODE(p.profile_dir.stat().st_mode) == 0o700


def test_state_missing_file_is_empty(tmp_path):
    assert load_state(tmp_path / "state.json") == {}


def test_state_round_trip_leaves_no_temp_files(tmp_path):
    path = tmp_path / "sub" / "state.json"
    save_state(path, {"meet_link": "https://meet.google.com/abc-defg-hij"})
    save_state(path, {"meet_link": "https://meet.google.com/xyz-abcd-efg"})
    assert load_state(path) == {"meet_link": "https://meet.google.com/xyz-abcd-efg"}
    assert [f.name for f in path.parent.iterdir()] == ["state.json"]


def test_state_corrupt_file(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_state(path)


def test_state_must_be_object(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps([1, 2]))
    with pytest.raises(ConfigError, match="JSON object"):
        load_state(path)
