"""The Chat tab's mic: a recording is written down by the speech service and sent like a typed message."""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mac_app import ctrl_backend as be
from mac_app.ctrl import CtrlPanel

WAV = base64.b64encode(b"RIFF\x00\x00\x00\x00WAVE").decode()


@pytest.fixture
def stt(monkeypatch):
    seen = {"reply": {"text": "  open   safari "}}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.update(body=self.rfile.read(int(self.headers["Content-Length"])), gate=self.headers["X-Bypass-Gate"])
            out = json.dumps(seen["reply"]).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("VIBI_STT_URL", f"http://127.0.0.1:{server.server_address[1]}/v1/audio/transcriptions")
    yield seen
    server.shutdown()


def test_recording_is_written_down(stt):
    assert be.transcribe(WAV) == {"text": "open safari"}
    assert b'filename="mic.wav"' in stt["body"] and b"RIFF" in stt["body"] and stt["gate"] == "1"


def test_silence_sends_nothing(stt):
    stt["reply"] = {"text": " "}
    with pytest.raises(ValueError, match="no words"):
        be.transcribe(WAV)


@pytest.mark.parametrize("audio", ["", "not base64!", base64.b64encode(b"ID3 something else").decode()])
def test_bad_recordings_are_refused(audio):
    with pytest.raises(ValueError, match="bad recording"):
        be.transcribe(audio)


def test_speech_service_down_is_said(monkeypatch):
    monkeypatch.setenv("VIBI_STT_URL", "http://127.0.0.1:1/")
    with pytest.raises(ValueError, match="did not answer"):
        be.transcribe(WAV)


@pytest.mark.parametrize("voice,marked", [(True, []), (False, ["vibi"])])
def test_mic_command_runs_like_a_typed_one_but_is_not_marked_typed(tmp_path, monkeypatch, voice, marked):
    ran, marks = [], []
    monkeypatch.setattr(be, "note_typed", lambda d, to, text: marks.append(to))
    panel = CtrlPanel(tmp_path, tmp_path, lambda text: ran.append(text) or True)
    code, _, _ = panel.handle("POST", "/api/chat", {"t": panel.token}, {"to": "vibi", "text": "open safari", "voice": voice})
    assert code == 200 and ran == ["open safari"] and marks == marked


def test_transcribe_needs_the_panel_token(tmp_path):
    panel = CtrlPanel(tmp_path, tmp_path, lambda text: True)
    assert panel.handle("POST", "/api/transcribe", {}, {"audio": WAV})[0] == 403
