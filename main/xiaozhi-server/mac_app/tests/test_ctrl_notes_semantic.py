"""Notes and todos are Chat entries, and search also finds entries close in meaning."""
import json
import time

import pytest

from mac_app import ctrl_backend as be
from mac_app import ctrl_items, ctrl_search, ctrl_semantic
from mac_app.ctrl import UI, CtrlPanel


class Store:
    def todos(self, d):
        return [{"id": "t1", "text": "buy oat milk", "done": False, "created": "2020-01-01 10:00:00"},
                {"id": "t2", "text": "old finished", "done": True, "created": "2020-01-01 10:00:00"}]


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(be, "_todo_store", lambda d: Store())
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "message_board.py").write_text("def messages(d, source=None):\n    return []\n")
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setenv("VIBI_MESSAGE_BOARD", str(tmp_path / "server" / "message_board.py"))
    (d / "notes.jsonl").write_text('{"id": "n1", "ts": "2020-01-02 09:00", "tag": "", "text": "cheap flights on friday"}\n')
    return d


def ids(rows):
    return {r["id"] for r in rows}


def test_open_todos_stay_and_old_notes_show_when_asked_for(data):
    assert ids(be.messages(data, hours=72)) == {"todo-t1"}
    assert ids(be.messages(data, hours=72, type="note")) == {"note-n1"}
    assert ids(be.messages(data, hours=0)) == {"todo-t1", "todo-t2", "note-n1"}
    assert be.messages(data, hours=0, type="conversation") == []
    note = be.messages(data, hours=0, type="note")[0]
    assert note["item"] == {"kind": "note", "id": "n1", "text": "cheap flights on friday"} and note["category"] == "Note"


def test_a_note_can_be_added_from_chat(data):
    panel = CtrlPanel(data, data, lambda t: pytest.fail("a note must not reach an agent"))
    code, _, body = panel.handle("POST", "/api/chat", {"t": panel.token}, {"to": "note", "text": "call the bank"})
    assert code == 200 and json.loads(body)["to"] == "note"
    assert "call the bank" in [r["body"] for r in be.messages(data, type="note")]
    assert panel.handle("POST", "/api/chat", {"t": panel.token}, {"to": "note", "text": " "})[0] == 400
    with pytest.raises(ValueError):
        ctrl_items.add_note(data, "x" * 5000)


def fake_embed(texts):
    groups = (("flight", "airfare"), ("milk", "groceries"))
    return [[1.0 if any(w in t for w in g) else 0.0 for g in groups] + [0.01] for t in texts]


def test_search_finds_similar_meaning_and_caches_vectors(data, monkeypatch):
    calls = []
    monkeypatch.setattr(ctrl_semantic, "embed", lambda t: calls.append(len(t)) or fake_embed(t))
    found = ctrl_search.search(data, "airfare")
    assert [(h["id"], h["meta"].startswith("Similar meaning")) for h in found["results"]] == [("n1", True)]
    assert found["note"] == ""
    calls.clear()
    ctrl_search.search(data, "airfare")
    assert calls == [1]  # only the query: entries come from the cache
    assert (data / "search_vectors.db").stat().st_mode & 0o777 == 0o600


def test_a_long_history_is_indexed_in_the_background(data, monkeypatch):
    monkeypatch.setattr(ctrl_semantic, "embed", fake_embed)
    texts = [f"flight {i}" for i in range(ctrl_semantic.NOW + 5)]
    near, left = ctrl_semantic.scores(data, "airfare", texts)
    assert near == {} and left == len(texts)
    for _ in range(100):
        near, left = ctrl_semantic.scores(data, "airfare", texts)
        if not left:
            break
        time.sleep(0.05)
    assert left == 0 and len(near) == len(texts)


def test_without_the_model_word_search_still_works(data, monkeypatch):
    monkeypatch.setenv("VIBI_EMBED_URL", "http://127.0.0.1:1/")
    found = ctrl_search.search(data, "flights")
    assert [h["id"] for h in found["results"]] == ["n1"] and found["note"].startswith("Matching words only")


def test_the_tabs_are_merged_into_chat():
    html = UI.read_text()
    assert 'TABS=[["chat","Chat"],["components","Components"]]' in html and "loadTodos" not in html
    assert '{label:"Note (save)",value:"note"}' in html
