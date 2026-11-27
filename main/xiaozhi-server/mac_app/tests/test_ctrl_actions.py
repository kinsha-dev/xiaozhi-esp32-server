"""The Actions tab lives in the Chat timeline: actions thread under their voice command, the rest stand on their own."""
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


def _a(i, ago, name, said="", source="voice", **kw):
    return {"id": f"a{i}", "ts": NOW - ago, "kind": "action", "name": name, "said": said, "args": "x=1", "status": "ok",
            "result": "done", "risky": False, "source": source, **kw}


@pytest.fixture
def data(tmp_path):
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "message_board.py").write_text("def messages(data_dir, source=None):\n    return []\n")
    d = tmp_path / "data"
    d.mkdir()
    cmd = lambda i, ago, said: {"id": f"c{i}", "ts": NOW - ago, "kind": "command", "said": said, "source": "voice"}
    (d / "history.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [
        cmd(1, 500, "turn the TV up"), _a(1, 499, "llm_input", "turn the TV up", "llm", text="sent text"),
        _a(2, 498, "tv_volume", "turn the TV up", risky=True),
        cmd(2, 400, "background talk"),
        cmd(3, 300, "mumble"), _a(3, 299, "llm_input", "mumble", "llm"),
        {"id": "r3", "ts": NOW - 298, "kind": "reply", "text": "Didn't catch that."},
        _a(4, 200, "task_run", "morning news", "schedule"), _a(5, 100, "ask_grok_bot", "", "cli", status="error")]))
    return d


def _view(data, **kw):
    return [(r["channel"], r["body"], r["recognized"], [a["title"] for a in r["actions"]]) for r in reversed(be.messages(data, **kw))]


def test_actions_thread_under_their_command_and_loose_ones_stand_alone(data):
    assert _view(data) == [("voice", "turn the TV up", True, ["llm_input", "tv_volume"]),
                           ("action", "task_run", None, ["task_run"]), ("action", "ask_grok_bot", None, ["ask_grok_bot"])]
    child = be.messages(data)[-1]["actions"][1]
    tab = next(r for r in be.history(data, "actions") if r["id"] == "a2")
    assert child == {**{k: tab[k] for k in ("id", "time", "title", "status", "detail", "risky", "source")}, "rerun": True}
    assert be.find_rerun(data, "actions", child["id"]) == {"text": "turn the TV up", "risky": True}


def test_recognized_filter(data):
    bad = [("voice", "background talk", False, []), ("voice", "mumble", False, ["llm_input"]),
           ("voice", "Didn't catch that.", False, [])]
    assert _view(data, action="unrecognized") == bad
    assert len(_view(data, action="all")) == 6
    assert _view(data, action="recognized") == _view(data)


def test_type_filter_covers_actions(data):
    assert [r["type"] for r in be.messages(data, type="grok")] == ["grok"]
    assert [r["body"] for r in be.messages(data, type="actions")] == ["ask_grok_bot", "task_run", "turn the TV up"]
    assert [r["body"] for r in be.messages(data, type="conversation")] == ["turn the TV up"]
    assert be.messages(data, direction="to") == []  # an action is neither sent nor received


def test_endpoint_passes_the_action_filter(data):
    p = CtrlPanel(data, data, lambda text: True)
    code, _, out = p.handle("GET", "/api/messages", {"t": p.token, "action": "unrecognized"}, {})
    assert code == 200 and len(json.loads(out)) == 3


def test_actions_tab_is_gone_and_old_links_open_chat():
    assert '["actions",' not in HTML and '("actions", "Actions")' not in (APP / "app.py").read_text()
    assert 'actions:"chat"' in HTML and '||"chat",timer' in HTML
    assert 'action:["recognized"]' in HTML


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_thread_follows_its_command(data):
    start = HTML.index("const CHAT_NAMES")
    code = HTML[start:HTML.index("/* ---- Reply menu", start)]
    script = f"""const replyTarget=()=>null;{code}
console.log(JSON.stringify(chatMessagesUpdate({json.dumps(be.messages(data, action="all"))}).updateDataModel.value))"""
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    assert [b["variant"] for b in out] == ["me", "thread", "me", "me", "thread", "reply", "actions", "actions"]
    assert [a["title"] for a in out[1]["actions"]] == ["llm_input", "tv_volume"]
    assert "unrecognized" in out[2]["meta"] and [b["variant"] for b in out][-2:] == ["actions", "actions"]
