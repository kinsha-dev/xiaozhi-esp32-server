import json

from mac_app.protocol import (
    abort_message,
    detect_message,
    hello_message,
    listen_message,
    parse_server_message,
)


def test_hello_declares_opus_16k_mono_60ms():
    m = json.loads(hello_message())
    assert m["type"] == "hello"
    assert m["version"] == 1
    assert m["transport"] == "websocket"
    assert m["audio_params"] == {"format": "opus", "sample_rate": 16000, "channels": 1, "frame_duration": 60}


def test_listen_start_uses_auto_mode():
    m = json.loads(listen_message("s1", "start"))
    assert m == {"session_id": "s1", "type": "listen", "state": "start", "mode": "auto"}


def test_detect_carries_text():
    m = json.loads(detect_message("s1", "hi bibi"))
    assert m["type"] == "listen" and m["state"] == "detect" and m["text"] == "hi bibi"
    assert m["session_id"] == "s1"


def test_abort():
    assert json.loads(abort_message("s1"))["type"] == "abort"


def test_parse_server_hello_extracts_session_and_audio_params():
    raw = json.dumps({"type": "hello", "session_id": "abc", "audio_params": {"sample_rate": 24000, "frame_duration": 60}})
    ev = parse_server_message(raw)
    assert ev.kind == "hello" and ev.session_id == "abc"
    assert ev.audio_params["sample_rate"] == 24000


def test_parse_tts_stop_state():
    ev = parse_server_message(json.dumps({"type": "tts", "state": "stop"}))
    assert (ev.kind, ev.state) == ("tts", "stop")


def test_parse_unknown_type_is_other():
    assert parse_server_message(json.dumps({"type": "weird"})).kind == "other"
    assert parse_server_message("not json").kind == "other"
