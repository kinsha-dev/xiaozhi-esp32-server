"""Reply on WhatsApp from the Chat tab. Nothing here opens WhatsApp: the commands are recorded."""
import json
import subprocess

import pytest

from mac_app import ctrl_whatsapp as wa
from mac_app.ctrl import CtrlPanel


@pytest.fixture
def data(tmp_path):
    (tmp_path / "whatsapp_contacts.json").write_text(json.dumps({"sample person": "+1 555 010 0000"}))
    return tmp_path


def send(data, run, phone="15550100000", contact="‎Sample Person", text="hi & bye"):
    return wa.send(data, contact, text, phone, run=run, wait=lambda s: None)


def test_names_and_numbers_resolve(data):
    assert wa.resolve(data, "‎Sample  Person") == {"contact": "Sample Person", "phone": "15550100000"}
    assert wa.resolve(data, "+44 20 7946 0000")["phone"] == "442079460000"
    for bad in ("", "Sample", "someone else", "123"):
        with pytest.raises(ValueError):
            wa.resolve(data, bad)


def test_send_opens_the_draft_presses_return_and_logs(data):
    calls = []
    assert send(data, lambda cmd, **kw: calls.append(cmd))["sent"]
    assert calls[0][-1] == "https://wa.me/15550100000?text=hi%20%26%20bye" and calls[1][0] == "osascript"
    assert json.loads((data / "whatsapp_messages.jsonl").read_text())["message"] == "hi & bye"


def test_nothing_is_sent_to_an_unconfirmed_number(data):
    with pytest.raises(ValueError, match="changed"):
        send(data, lambda *a, **k: pytest.fail("ran"), phone="000")


def test_return_is_not_logged_when_whatsapp_is_not_in_front(data):
    def run(cmd, **kw):
        if cmd[0] == "osascript":
            raise subprocess.CalledProcessError(1, cmd)

    with pytest.raises(ValueError, match="draft is open"):
        send(data, run)
    assert not (data / "whatsapp_messages.jsonl").exists()


def test_the_panel_only_looks_up_until_confirmed(data, monkeypatch):
    monkeypatch.setattr(wa, "send", lambda *a: pytest.fail("sent without confirmation"))
    panel = CtrlPanel(data, data, lambda t: True)
    body = {"contact": "sample person", "text": "hi", "confirmed": "yes"}
    code, _, out = panel.handle("POST", "/api/whatsapp", {"t": panel.token}, body)
    assert code == 200 and json.loads(out)["phone"] == "15550100000"
    assert panel.handle("POST", "/api/whatsapp", {}, body)[0] == 403


def test_the_sender_is_read_from_the_notification(data):
    (data / "whatsapp_contacts.json").write_text(json.dumps({"sample": "+15550100001", "sample new": "+15550100000",
                                                             "dr. who": "+15550100002"}))
    texts = ["\u200eSample New. Like 8-10 kids would be saying. More", "Sample: see you", "Dr. Who. hello",
             "Sample New \u2014 sub\nbody", "New notification from whatsapp: Sample New. hi", "Stranger Person. hi there", ""]
    assert wa.senders(data, texts) == ["Sample New", "Sample", "Dr. Who", "Sample New", "Sample New", "Stranger Person", ""]


def test_received_rows_carry_the_sender(data, monkeypatch):
    from mac_app import ctrl_backend as be

    (data / "server").mkdir()
    (data / "server" / "message_board.py").write_text(
        "def messages(d, source=None):\n    return [{'id': 'w', 'ts': 9e9, 'source': 'Notifications \u00b7 Phone', 'channel': 'whatsapp', "
        "'direction': 'to', 'body': 'Sample Person. a long message', 'status': ''}]\n")
    monkeypatch.setenv("VIBI_MESSAGE_BOARD", str(data / "server" / "message_board.py"))
    assert [r["contact"] for r in be.messages(data) if r["channel"] == "whatsapp"] == ["Sample Person"]
