"""The Commands tab lives on the command entries of the Chat timeline: Read and Rerun."""
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from mac_app import ctrl_backend as be
from mac_app.ctrl import CtrlPanel

NOW = time.time()
APP = Path(__file__).parents[1]
HTML = (APP / "ctrl_ui" / "index.html").read_text()
LOG = """import json, re
def record(*a, **k):
    pass
def is_risky(name, said="", args=None):
    return bool(re.search(r"\\bsend\\b", said))
"""


@pytest.fixture
def data(tmp_path):
    (tmp_path / "server" / "plugin").mkdir(parents=True)
    board = [{"id": "i1", "source": "Claude", "channel": "inbox", "direction": "from", "ts": NOW - 50, "time": "t",
              "status": "read", "summary": "Commit it", "body": "Commit it", "pair_id": ""},
             {"id": "s1", "source": "Vibi", "channel": "typed", "direction": "to", "ts": NOW - 5000, "time": "t",
              "status": "done", "summary": "", "body": "You: ping\n\nVibi: pong", "pair_id": ""}]
    (tmp_path / "server" / "message_board.py").write_text(f"def messages(data_dir, source=None):\n    return {board!r}\n")
    (tmp_path / "server" / "plugin" / "history_log.py").write_text(LOG)
    d = tmp_path / "data"
    d.mkdir()
    cmd = lambda i, ago, said: {"id": f"c{i}", "ts": NOW - ago, "kind": "command", "said": said, "source": "voice"}
    act = lambda i, ago, name, said, **kw: {"id": f"a{i}", "ts": NOW - ago, "kind": "action", "name": name, "said": said,
                                            "args": "", "status": "ok", "result": "volume 8", "risky": False, "source": "voice", **kw}
    (d / "history.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [
        cmd(1, 900, "turn the TV up"), act(1, 899, "tv_volume", "turn the TV up"),
        {"id": "r1", "ts": NOW - 898, "kind": "reply", "text": "Louder now."},
        cmd(2, 800, "send the report"), cmd(3, 700, "quietly delete"), act(3, 699, "mac_control", "quietly delete", risky=True),
        cmd(4, 600, "Hey, hello there")]))
    return d


def _commands(data, **kw):
    return {r["id"]: r["command"] for r in be.messages(data, type="commands", action="all", hours=0, **kw)}


def test_every_command_entry_can_be_read_and_voice_ones_rerun(data):
    c = _commands(data)
    assert set(c) == {"cmd-c1", "cmd-c2", "cmd-c3", "cmd-c4", "i1", "s1"}
    assert c["cmd-c1"] == {"text": "turn the TV up", "rerun": True, "risky": False, "result": ["Louder now."]}
    assert c["cmd-c3"]["result"] == ["mac_control (ok): volume 8"]  # no reply: the actions are the result
    assert c["cmd-c2"]["result"] == []
    assert c["i1"] == {"text": "Commit it", "rerun": False, "risky": False, "result": []}  # as in the old tab
    assert c["s1"] == {"text": "ping", "rerun": True, "risky": False, "result": ["pong"]}


def test_risky_commands_ask_first(data):
    c = _commands(data)
    assert c["cmd-c2"]["risky"]  # risky words
    assert c["cmd-c3"]["risky"]  # it ran a risky action
    assert not c["cmd-c1"]["risky"]


def test_rerun_from_a_chat_entry_uses_the_same_path(data):
    sent = []
    p = CtrlPanel(data, data, lambda text: sent.append(text) or True)
    post = lambda i: json.loads(p.handle("POST", "/api/rerun", {"t": p.token}, {"kind": "chat", "id": i})[2])
    assert post("cmd-c1") == {"ok": True, "risky": False, "note": "Replayed as a voice request."}
    out = post("cmd-c2")
    assert out["risky"] and "confirm out loud" in out["note"]
    assert post("s1")["ok"] and sent == ["turn the TV up", "send the report", "ping"]
    for no in ("i1", "nope", "say-r1"):
        assert not post(no)["ok"]
    assert len(sent) == 3
    assert p.handle("POST", "/api/rerun", {}, {"kind": "chat", "id": "cmd-c1"})[0] == 403


def test_commands_tab_is_gone_and_old_links_show_the_commands(data):
    assert '["commands",' not in HTML and '("commands", "Commands")' not in (APP / "app.py").read_text()
    assert 'commands:"chat"' in HTML and 'value:"commands"' in HTML


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_bubbles_carry_the_command(data):
    start = HTML.index("const CHAT_NAMES")
    code = HTML[start:HTML.index("/* ---- Reply menu", start)]
    script = f"""const replyTarget=()=>null;{code}
console.log(JSON.stringify(chatMessagesUpdate({json.dumps(be.messages(data, action="all", hours=0))}).updateDataModel.value))"""
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    got = {b["command"]["id"]: b for b in out if b.get("command")}
    assert set(got) == {"cmd-c1", "cmd-c2", "cmd-c3", "cmd-c4", "i1", "s1"}
    assert all(b["variant"] == "me" for b in got.values())
    assert got["i1"]["command"]["status"] == "read by Claude" and got["cmd-c2"]["command"]["status"] == "unrecognized"
