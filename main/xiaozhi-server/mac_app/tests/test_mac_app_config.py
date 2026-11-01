from pathlib import Path

from mac_app.config import load


def test_defaults():
    c = load({})
    assert c.server_url == "ws://127.0.0.1:8000/xiaozhi/v1/"
    assert c.wake_phrase == "hi bibi"
    assert c.data_dir == Path(__file__).resolve().parents[2] / "data"
    assert c.speaker_rate == 24000
    assert c.idle_timeout == 300


def test_env_overrides(tmp_path):
    c = load({"MAC_APP_SERVER": "ws://h:1/x/", "MAC_APP_WAKE": "hey buddy", "VOICE_BOX_DATA": str(tmp_path)})
    assert (c.server_url, c.wake_phrase, c.data_dir) == ("ws://h:1/x/", "hey buddy", tmp_path)
