"""Chat tab search over chat, todos and notes, and editing a note or todo."""
import json

import pytest

from mac_app import ctrl_backend as be
from mac_app import ctrl_search as se
from mac_app import ctrl_rank
from mac_app.ctrl import CtrlPanel


class Store:
    LIMIT = 500

    def __init__(self):
        self.rows = [{"id": "t1", "text": "buy milk", "done": False, "created": "2026-11-28 10:00:00"}]

    def todos(self, d):
        return self.rows

    def _change(self, d, id_, change):
        for i, r in enumerate(self.rows):
            if r["id"] == id_:
                self.rows[i] = change(dict(r))
                return self.rows[i]
        raise KeyError(id_)


@pytest.fixture
def data(tmp_path, monkeypatch):
    store = Store()
    monkeypatch.setattr(be, "_todo_store", lambda d: store)

    def no_model(*a):
        raise ctrl_rank.Unavailable("off in tests")

    monkeypatch.setattr(ctrl_rank, "rank", no_model)
    monkeypatch.setattr(be, "messages", lambda d, **kw: [
        {"id": "c1", "ts": 5, "time": "t", "category": "Claude", "direction": "to", "body": "Milk prices rose"},
        {"id": "c2", "ts": 6, "time": "t", "category": "Claude", "direction": "to", "body": "unrelated"}])
    (tmp_path / "notes.jsonl").write_text('{"id": "n1", "ts": "x", "tag": "", "text": "oat milk is good", "extra": 1}\n'
                                          'not json\n{"id": "n2", "ts": "y", "text": "other"}\n')
    return tmp_path


def test_search_covers_chat_todos_and_notes(data):
    assert [(h["kind"], h["id"]) for h in se.search(data, "MILK")["results"]] == [("todo", "t1"), ("note", "n1"), ("chat", "c1")]
    assert se.search(data, "milk good")["results"][0]["id"] == "n1"  # near matches follow
    assert [h["id"] for h in se.search(data, "milk", "chat")["results"]] == ["c1"]
    assert se.search(data, "  ")["results"] == []


def test_editing_a_note_keeps_everything_else(data):
    se.edit(data, "note", "n1", "soy milk", "oat milk is good")
    lines = (data / "notes.jsonl").read_text().split("\n")
    assert json.loads(lines[0]) == {"id": "n1", "ts": "x", "tag": "", "text": "soy milk", "extra": 1}
    assert lines[1] == "not json" and json.loads(lines[2])["text"] == "other" and lines[3] == ""


def test_editing_a_todo(data):
    assert se.edit(data, "todo", "t1", " buy\n oat milk ", "buy milk")["text"] == "buy oat milk"


@pytest.mark.parametrize("kind,id_,old", [("note", "n1", "oat milk is good"), ("todo", "t1", "buy milk")])
def test_stale_or_empty_edits_change_nothing(data, kind, id_, old):
    with pytest.raises(ValueError, match="changed meanwhile"):
        se.edit(data, kind, id_, "new", "something older")
    with pytest.raises(ValueError, match="empty"):
        se.edit(data, kind, id_, " ", old)
    assert [h["text"] for h in se.search(data, "milk", kind)["results"]] == [old]


def test_unknown_things_are_refused(data):
    with pytest.raises(KeyError):
        se.edit(data, "note", "nope", "x", "y")
    with pytest.raises(ValueError):
        se.edit(data, "chat", "c1", "x", "y")


def test_panel_routes_need_the_token(data):
    panel = CtrlPanel(data, data, lambda t: True)
    assert panel.handle("GET", "/api/search", {"q": "milk"})[0] == 403
    code, _, body = panel.handle("GET", "/api/search", {"t": panel.token, "q": "milk", "scope": "note"})
    assert code == 200 and json.loads(body)["results"][0]["id"] == "n1"
    edit = {"kind": "note", "id": "n1", "text": "x", "old": "stale"}
    assert panel.handle("POST", "/api/edit", {"t": panel.token}, edit)[0] == 400
