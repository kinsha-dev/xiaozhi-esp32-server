import json
import os
from pathlib import Path

import pytest

from mac_app import ctrl_backend as be
from mac_app.ctrl import UI, CtrlPanel

# the store lives in the voice box repo (next to the live data dir); VIBI_TODO_STORE points at another checkout
STORE = Path(os.environ.get("VIBI_TODO_STORE") or Path.home() / "esp/voice-note-box/server/todo_store.py")
needs_store = pytest.mark.skipif(not STORE.exists(), reason="voice box todo store not found (set VIBI_TODO_STORE)")


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBI_TODO_STORE", str(STORE))
    monkeypatch.delenv("VIBI_MESSAGE_BOARD", raising=False)
    ran = []
    p = CtrlPanel(tmp_path, tmp_path, rerun=lambda text: ran.append(text) or True)
    p.ran = ran
    return p


def call(p, method, path, body=None):
    code, _, data = p.handle(method, path, {"t": p.token}, body)
    return code, json.loads(data)


def todos(p):
    return call(p, "GET", "/api/todos")[1]["todos"]


@needs_store
def test_add_done_reopen_delete(panel):
    assert todos(panel) == []
    code, row = call(panel, "POST", "/api/todos", {"op": "add", "text": "buy milk"})
    assert code == 200 and row["todo"]["source"] == "panel"
    id_ = row["todo"]["id"]
    assert call(panel, "POST", "/api/todos", {"op": "done", "id": id_})[1]["todo"]["done"] is True
    assert call(panel, "POST", "/api/todos", {"op": "reopen", "id": id_})[1]["todo"]["done"] is False
    assert call(panel, "POST", "/api/todos", {"op": "delete", "id": id_})[0] == 200
    assert todos(panel) == []


@needs_store
def test_bad_requests(panel):
    assert call(panel, "POST", "/api/todos", {"op": "add", "text": " "})[0] == 400
    assert call(panel, "POST", "/api/todos", {"op": "done", "id": "nope"})[0] == 404
    assert call(panel, "POST", "/api/todos", {"op": "explode"})[0] == 400
    assert panel.handle("GET", "/api/todos", {"t": "wrong"})[0] == 403


@needs_store
@pytest.mark.parametrize("to,text", [("todo", "call mom"), ("vibi", "add a todo call mom"), ("claude", "todo: call mom"),
                                     ("grok", "Todo: call mom")])
def test_chat_adds_todo_without_reaching_an_agent(panel, to, text):
    code, res = call(panel, "POST", "/api/chat", {"to": to, "text": text})
    assert code == 200 and res["to"] == "todo" and "call mom" in res["note"]
    assert [(t["text"], t["source"]) for t in todos(panel)] == [("call mom", "chat")]
    assert panel.ran == [] and not (panel.data_dir / "to_claude.jsonl").exists() and not (panel.data_dir / "grok").exists()


@needs_store
def test_chat_lists_and_completes(panel):
    call(panel, "POST", "/api/chat", {"to": "todo", "text": "water plants"})
    assert "water plants" in call(panel, "POST", "/api/chat", {"to": "vibi", "text": "list my todos"})[1]["note"]
    call(panel, "POST", "/api/chat", {"to": "todo", "text": "complete todo water plants"})
    assert [t["done"] for t in todos(panel)] == [True]


@needs_store
def test_chat_leaves_other_messages_alone(panel):
    # a request to an agent that merely mentions todos still goes to the agent
    code, res = call(panel, "POST", "/api/chat", {"to": "claude", "text": "add a todo tab to the panel"})
    assert code == 200 and res["to"] == "claude" and todos(panel) == []
    assert call(panel, "POST", "/api/chat", {"to": "vibi", "text": "what time is it"})[1]["to"] == "vibi"
    assert call(panel, "POST", "/api/chat", {"to": "computer", "text": "add a todo in Reminders"})[1]["to"] == "computer"
    assert todos(panel) == [] and len(panel.ran) == 2


@needs_store
def test_chat_todo_rejects_files_and_empty_text(panel):
    f = {"name": "a.txt", "data": "aGk="}
    assert call(panel, "POST", "/api/chat", {"to": "todo", "text": "x", "file": f})[0] == 400
    assert call(panel, "POST", "/api/chat", {"to": "todo", "text": " "})[0] == 400
    assert todos(panel) == []


@needs_store
def test_chat_reply_is_filed_on_the_board(panel, monkeypatch):
    board = STORE.with_name("message_board.py")
    monkeypatch.setenv("VIBI_MESSAGE_BOARD", str(board))
    call(panel, "POST", "/api/chat", {"to": "todo", "text": "call mom"})
    rows = [r for r in be.messages(panel.data_dir) if r["source"] == "Todo"]
    assert len(rows) == 1 and "Q: call mom" in rows[0]["body"] and "A: Added" in rows[0]["body"]


def test_without_the_store(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBI_TODO_STORE", str(tmp_path / "missing.py"))
    p = CtrlPanel(tmp_path, tmp_path, rerun=lambda text: True)
    code, res = call(p, "GET", "/api/todos")
    assert code == 400 and "voice note box" in res["error"]
    assert call(p, "POST", "/api/chat", {"to": "todo", "text": "x"})[0] == 400
    assert call(p, "POST", "/api/chat", {"to": "claude", "text": "todo: x"})[1]["to"] == "claude"


def test_ui_has_the_tab():
    html = UI.read_text()
    assert '["todo","Todo"]' in html and "/api/todos" in html and '{label:"Todo list",value:"todo"}' in html
