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


def test_messages_endpoint_lists_every_source_with_a_category(tmp_path, monkeypatch):
    import json
    from mac_app import ctrl_backend as be

    server = tmp_path / "server"
    server.mkdir()
    (server / "message_board.py").write_text(
        "def messages(data_dir, source=None):\n"
        "    return [{'id':'1','source':'Grok Bot','channel':'job-hunt','direction':'to','ts':2,'time':'t','status':'done','summary':'s','body':'b','pair_id':''},"
        "{'id':'2','source':'WhatsApp','channel':'whatsapp','direction':'from','ts':1,'time':'t','status':'sent','summary':'w','body':'w','pair_id':''}]\n")
    data = tmp_path / "data"
    data.mkdir()
    rows = be.messages(data, hours=0)
    assert [r["category"] for r in rows] == ["Grok Bot · job-hunt", "WhatsApp"]
    assert be.messages(data) == []  # ts 1 and 2 are older than 48 hours
    assert be.messages(tmp_path / "nowhere" / "data") == []


def test_chat_writes_the_claude_inbox_and_grok_request(tmp_path):
    import json
    import pytest
    from mac_app import ctrl_backend as be

    out = be.chat(tmp_path, "claude", " review PR 13 ")
    row = json.loads((tmp_path / "to_claude.jsonl").read_text().splitlines()[0])
    assert (row["text"], row["status"], row["project"], row["id"]) == ("review PR 13", "new", "voice box", out["id"])
    out = be.chat(tmp_path, "grok", "find a flight")
    req = json.loads((tmp_path / "grok" / "requests" / f"{out['id']}.json").read_text())
    assert (req["kind"], req["prompt"]) == ("task", "find a flight")
    assert not list((tmp_path / "grok" / "requests").glob(".*.tmp"))
    for bad in (("nobody", "x"), ("claude", "  "), ("grok", "x" * 5000)):
        with pytest.raises(ValueError):
            be.chat(tmp_path, *bad)


def test_chat_computer_use_replays_a_voice_request(tmp_path):
    sent = []
    p = CtrlPanel(tmp_path, tmp_path, lambda text: sent.append(text) or True)
    code, _, out = p.handle("POST", "/api/chat", {"t": p.token}, {"to": "computer", "text": " open  Safari "})
    assert code == 200 and sent == ["Use the computer to open Safari"]
    assert p.handle("POST", "/api/chat", {"t": p.token}, {"to": "computer", "text": "  "})[0] == 400
    busy = CtrlPanel(tmp_path, tmp_path, lambda text: False)
    assert busy.handle("POST", "/api/chat", {"t": busy.token}, {"to": "computer", "text": "x"})[0] == 409


def _fake_voice_box(tmp_path, monkeypatch, answer):
    """A voice-box repo next to data/ with stub gemini_ask + message_board modules."""
    import base64  # noqa: F401
    root = tmp_path / "vb"
    (root / "server").mkdir(parents=True)
    (root / "server" / "gemini_ask.py").write_text(
        "NOT_SET_UP = 'Gemini is not set up.'\n"
        "def gemini_key(d): return 'k'\n"
        "def openrouter_key(d): return ''\n"
        "def load_file(p):\n    p = str(p)\n    return (p.rsplit('/', 1)[-1], 'text/plain', open(p, 'rb').read())\n"
        f"def answer(d, q, file=None):\n    {answer}\n")
    (root / "server" / "message_board.py").write_text(
        "import json\n"
        "def post_message(text, data_dir, **kw):\n"
        "    open(str(data_dir) + '/posted.json', 'w').write(json.dumps({'text': text, **kw}))\n")
    data = root / "data"
    data.mkdir()
    return data


def _wait_for(path):
    import time
    for _ in range(100):
        if path.exists():
            return
        time.sleep(0.02)


def test_chat_gemini_answers_in_background_and_posts_to_board(tmp_path):
    import base64
    import json
    from mac_app import ctrl_backend as be

    data = _fake_voice_box(tmp_path, None, "return ('42 ' + (file[2].decode() if file else ''), 'openrouter')")
    out = be.chat(data, "gemini", "what is in this", {"name": "../notes.txt", "data": base64.b64encode(b"hello").decode()})
    assert out["to"] == "gemini"
    _wait_for(data / "posted.json")
    row = json.loads((data / "posted.json").read_text())
    assert (row["source"], row["channel"], row["direction"], row["status"]) == ("Gemini", "gemini", "to", "done")
    assert "A: 42 hello (via OpenRouter)" in row["text"] and "[file: " in row["text"]
    saved = list((data / "uploads").iterdir())
    assert len(saved) == 1 and saved[0].name.endswith("-notes.txt") and saved[0].read_bytes() == b"hello"


def test_chat_gemini_failure_is_posted_failed(tmp_path):
    import json
    from mac_app import ctrl_backend as be

    data = _fake_voice_box(tmp_path, None, "raise RuntimeError('Gemini failed: HTTP 500')")
    be.chat(data, "gemini", "hi")
    _wait_for(data / "posted.json")
    row = json.loads((data / "posted.json").read_text())
    assert row["status"] == "failed" and "HTTP 500" in row["text"]


def test_chat_gemini_without_voice_box_or_files_raises(tmp_path):
    import base64
    import pytest
    from mac_app import ctrl_backend as be

    with pytest.raises(ValueError, match="Gemini is not available"):
        be.chat(tmp_path / "nowhere" / "data", "gemini", "hi")
    data = _fake_voice_box(tmp_path, None, "return ('x', 'gemini')")
    with pytest.raises(ValueError):
        be.chat(data, "gemini", "hi", {"name": "a.txt", "data": "!!not base64!!"})
    big = base64.b64encode(b"x" * (be.FILE_LIMIT + 1)).decode()
    with pytest.raises(ValueError, match="too large"):
        be.chat(data, "gemini", "hi", {"name": "a.bin", "data": big})


def test_chat_file_path_is_added_for_claude_and_grok(tmp_path):
    import base64
    import json
    from mac_app import ctrl_backend as be

    f = {"name": "spec.pdf", "data": base64.b64encode(b"%PDF").decode()}
    be.chat(tmp_path, "claude", "read this", dict(f))
    row = json.loads((tmp_path / "to_claude.jsonl").read_text().splitlines()[0])
    assert "[attached file: " in row["text"] and "spec.pdf" in row["text"]
    out = be.chat(tmp_path, "grok", "read this", dict(f))
    req = json.loads((tmp_path / "grok" / "requests" / f"{out['id']}.json").read_text())
    assert req["file"].endswith("spec.pdf") and "[attached file: " in req["prompt"]
