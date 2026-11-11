import json
import time
from pathlib import Path

from mac_app import ctrl_backend as be
from mac_app.ctrl import CtrlPanel


def w(p, rows):
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def seed(d: Path):
    w(d / "notes.jsonl", [{"id": "a1", "ts": "2026-10-31 12:46", "tag": "meeting", "text": "Budget"}])
    w(d / "reminders.jsonl", [{"id": "r1", "created": "2026-10-31 12:46", "due": "2026-10-31 12:48", "text": "Stretch", "done": True}])
    w(d / "to_claude.jsonl", [{"id": "i1", "ts": "2026-10-31T15:08:31", "text": "Commit it", "status": "read"}])
    now = time.time()
    w(d / "history.jsonl", [
        {"id": "h1", "ts": now - 5, "kind": "action", "name": "get_time", "said": "what time is it", "args": "", "status": "ok", "result": "3pm", "risky": False, "source": "voice"},
        {"id": "h2", "ts": now - 3, "kind": "action", "name": "computer_use", "said": "open safari", "args": "task=open safari", "status": "ok", "result": "started", "risky": True, "source": "voice"},
        {"id": "h3", "ts": now - 1, "kind": "command", "said": "what time is it", "source": "voice"}])


def test_history_kinds(tmp_path):
    seed(tmp_path)
    acts = be.history(tmp_path, "actions")
    assert [a["title"] for a in acts] == ["computer_use", "get_time"] and acts[0]["risky"]
    assert {c["source"] for c in be.history(tmp_path, "commands")} == {"voice", "inbox"}
    notes = be.history(tmp_path, "notes")
    assert {n["status"] for n in notes} == {"saved", "done"}


def test_find_rerun(tmp_path):
    seed(tmp_path)
    assert be.find_rerun(tmp_path, "actions", "h1") == {"text": "what time is it", "risky": False}
    assert be.find_rerun(tmp_path, "actions", "h2")["risky"] is True
    assert be.find_rerun(tmp_path, "commands", "i1") is None


def test_components(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "server.log").write_text("ok\n2026 ERROR boom token=abcdef\nfine\n")
    (tmp_path / "ntfy_topic").write_text("my-topic")
    (tmp_path / "board_heartbeat").write_text(str(time.time() - 10))
    st = lambda label, *a: {"running": label.endswith("server"), "pid": 7 if label.endswith("server") else None, "detail": ""}
    cs = {c["key"]: c for c in be.components(tmp_path, tmp_path, status=st)}
    assert cs["server"]["running"] and cs["server"]["pid"] == 7 and "boom" in cs["server"]["error"] and "abcdef" not in cs["server"]["error"]
    assert not cs["whisper"]["running"] and cs["board"]["running"] and cs["ntfy"]["detail"] == "my-topic"
    assert cs["server"]["restart"] == "com.voicebox.server"


def test_launchd_parse():
    out = "\tstate = running\n\tpid = 91787\n"
    assert be.launchd_status("x", 501, run=lambda c: out) == {"running": True, "pid": 91787, "detail": "running"}
    assert be.launchd_status("x", 501, run=lambda c: "")["running"] is False


def test_restart_command():
    calls = []
    r = be.restart("whisper", uid=501, kick=lambda c: (calls.append(c), (0, ""))[1])
    assert r["ok"] and calls == [["launchctl", "kickstart", "-k", "gui/501/com.voicebox.whisper"]]
    assert not be.restart("bogus", uid=501)["ok"]


def test_panel_api_and_rerun(tmp_path):
    seed(tmp_path)
    said = []
    p = CtrlPanel(tmp_path, tmp_path, lambda t: said.append(t) or True)
    assert p.handle("GET", "/api/history", {"kind": "actions"})[0] == 403  # no token
    code, _, body = p.handle("GET", "/api/history", {"kind": "actions", "t": p.token})
    assert code == 200 and len(json.loads(body)) == 2
    _, _, body = p.handle("POST", "/api/rerun", {"t": p.token}, {"kind": "actions", "id": "h2"})
    r = json.loads(body)
    assert r["ok"] and r["risky"] and said == ["open safari"]  # replayed as a voice turn, so the spoken yes still applies
    assert not json.loads(p.handle("POST", "/api/rerun", {"t": p.token}, {"kind": "commands", "id": "i1"})[2])["ok"]


def test_style_get_set_and_api(tmp_path):
    assert be.get_style(tmp_path) == "flirty"  # default
    p = CtrlPanel(tmp_path, tmp_path, lambda t: True)
    _, _, body = p.handle("POST", "/api/style", {"t": p.token}, {"style": "plain"})
    assert json.loads(body)["style"] == "plain" and (tmp_path / "voice_style").read_text() == "plain"
    assert json.loads(p.handle("GET", "/api/style", {"t": p.token})[2])["style"] == "plain"
    assert p.handle("POST", "/api/style", {"t": p.token}, {"style": "rude"})[0] == 500
