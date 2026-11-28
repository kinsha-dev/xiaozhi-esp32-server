"""Send to Siri from the Chat tab: the voice-box module does the Shortcut, the panel returns its reply."""
import json

import pytest

from mac_app import ctrl_backend as be
from mac_app.ctrl import CtrlPanel
from mac_app.ctrl import UI


def _box(tmp_path):
    root = tmp_path / "vb"
    (root / "server").mkdir(parents=True)
    (root / "server" / "siri_chat.py").write_text(
        "def chat_turn(data_dir, text, **kw):\n"
        "    return {'ok': True, 'reply': 'Sent to Siri: ' + text + '\\nShortcut \"Send to Siri\" finished (exit 0).',"
        " 'spoken': 'Sent that to Siri.'}\n"
    )
    data = root / "data"
    data.mkdir()
    return data


def test_siri_chat_returns_the_shortcut_detail(tmp_path):
    data = _box(tmp_path)
    out = be.siri_chat(data, " what's the weather ")
    assert out["to"] == "siri" and out["ok"] is True
    assert "what's the weather" in out["note"] and "exit 0" in out["note"]
    panel = CtrlPanel(data, data, lambda text: True)
    code, _, raw = panel.handle("POST", "/api/chat", {"t": panel.token}, {"to": "siri", "text": "hello"})
    assert code == 200 and "exit 0" in json.loads(raw)["note"]
    assert panel.handle("POST", "/api/chat", {"t": panel.token}, {"to": "siri", "text": " "})[0] == 400
    refused = panel.handle("POST", "/api/chat", {"t": panel.token}, {"to": "siri", "text": "x", "file": {"name": "a.txt", "data": "YQ=="}})
    assert refused[0] == 400 and b"files" in refused[2]


def test_siri_without_the_voice_box_module_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="not available"):
        be.siri_chat(tmp_path / "no" / "data", "hi")


def test_chat_tab_has_siri_mode_and_the_button():
    html = UI.read_text()
    assert '{label:"Siri",value:"siri"}' in html
    assert "Send to Siri" in html
    assert 'name:"send_siri"' in html
