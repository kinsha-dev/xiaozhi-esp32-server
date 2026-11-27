"""The Chat tab is the one timeline (the Message board tab was merged into it), filtered by message type and direction."""
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from mac_app import ctrl_backend as be
from mac_app.ctrl import CtrlPanel

HTML = (Path(__file__).parents[1] / "ctrl_ui" / "index.html").read_text()
NOW = time.time()

ROWS = [  # (source, channel, direction, body, expected type)
    ("Claude", "outbox", "to", "done", "claude"),
    ("Claude", "computer-use", "from", "open Safari", "computer"),
    ("Grok Bot", "handoff", "to", "answer", "grok"),
    ("Grok Bot", "job-hunt", "to", "a job", "jobs"),
    ("Gemini", "gemini", "to", "Q: hi\n\nA: hello", "gemini"),
    ("Vibi", "typed", "to", "ok", "vibi"),
    ("Todo", "todo", "to", "Q: add\n\nA: added", "todo"),
    ("WhatsApp", "whatsapp", "from", "To a: b", "whatsapp"),
    ("Notifications · Phone", "whatsapp", "to", "ping", "notifications"),
    ("Mail digest", "digest", "to", "mail", "mail"),
    ("tv_youtube", "stdio", "to", "video", "other"),
]


def _board(tmp_path, rows):
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "message_board.py").write_text(f"def messages(data_dir, source=None):\n    return {rows!r}\n")
    data = tmp_path / "data"
    data.mkdir()
    return data


def _row(i, source, channel, direction, body, ts=None):  # equal times keep the listed order
    return {"id": str(i), "source": source, "channel": channel, "direction": direction, "ts": ts or NOW,
            "time": "t", "status": "done", "summary": body, "body": body, "pair_id": ""}


@pytest.fixture
def data(tmp_path):
    return _board(tmp_path, [_row(i, *r[:4]) for i, r in enumerate(ROWS)])


def test_every_row_has_a_type(data):
    assert [r["type"] for r in be.messages(data)] == [r[4] for r in ROWS]


def test_filter_by_type_and_conversation(data):
    for kind in {r[4] for r in ROWS}:
        assert [r["type"] for r in be.messages(data, type="all" if kind == "all" else kind)] == [kind]
    assert {r["type"] for r in be.messages(data, type="conversation")} == {"claude", "grok", "gemini", "vibi", "todo", "computer"}
    assert len(be.messages(data, type="all")) == len(ROWS)


def test_filter_by_direction_keeps_question_and_answer_rows_both_ways(data):
    assert [r["type"] for r in be.messages(data, direction="from")] == ["computer", "gemini", "todo", "whatsapp"]
    assert "whatsapp" not in [r["type"] for r in be.messages(data, direction="to")]


def test_filter_applies_before_the_limit(tmp_path):
    rows = [_row(i, "Claude", "outbox", "to", "x") for i in range(5)] + [_row(9, "Mail digest", "digest", "to", "m")]
    data = _board(tmp_path, rows)
    assert [r["id"] for r in be.messages(data, limit=3, type="mail")] == ["9"]


def test_endpoint_passes_the_filters(data):
    p = CtrlPanel(data, data, lambda text: True)
    code, _, out = p.handle("GET", "/api/messages", {"t": p.token, "type": "jobs", "direction": "to", "hours": "0"}, {})
    assert code == 200 and [r["type"] for r in json.loads(out)] == ["jobs"]


def test_board_tab_is_gone_and_old_links_open_chat():
    assert '["board",' not in HTML and '("board", "Message board")' not in (Path(__file__).parents[1] / "app.py").read_text()
    assert "Message board" not in HTML
    assert 'board:"chat"' in HTML


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_timeline_bubbles(data):
    """The page's own row -> bubble function: every source shows, replies only on bot responses, direction splits Q/A rows."""
    start = HTML.index("const CHAT_NAMES")
    code = HTML[start:HTML.index("/* ---- Reply menu", start)]
    reply = re.search(r"^const REPLY_FROM=.*\n(?:const replyTarget=.*\n)", HTML, re.M).group(0)
    script = f"""{reply}{code}
const rows={json.dumps(be.messages(data))};
console.log(JSON.stringify({{all:chatMessagesUpdate(rows).updateDataModel.value,
 sent:chatMessagesUpdate(rows,"from").updateDataModel.value,none:chatMessagesUpdate([]).updateDataModel.value}}))"""
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    texts = [b["text"] for b in out["all"]]
    assert all(t in texts for t in ("done", "open Safari", "a job", "To a: b", "ping", "mail", "video", "ok", "hi", "hello"))
    by = {b["text"]: b for b in out["all"]}
    assert by["done"]["reply"]["to"] == "claude" and by["a job"]["reply"]["to"] == "grok" and by["ok"]["reply"]
    assert not any(by[t].get("reply") for t in ("ping", "mail", "To a: b", "open Safari", "video", "added"))
    assert by["open Safari"]["variant"] == "me" and by["ping"]["variant"] == "other"
    assert by["ping"]["meta"] == "Notifications · Phone · whatsapp · t · done" and by["ping"]["clamp"]
    assert [b["text"] for b in out["sent"] if b["text"] in ("hi", "hello", "add", "added")] == ["add", "hi"]
    assert out["none"][0]["variant"] == "note"
