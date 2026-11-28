"""Notes and todos are Chat entries, and search is ranked by Jev."""
import json

import pytest

from mac_app import ctrl_backend as be
from mac_app import ctrl_items, ctrl_rank, ctrl_search
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


def test_fuzzy_candidates_allow_typos_and_other_endings():
    texts = ["cheap flights on friday", "buy oat milk", "the flight was delayed", "nothing here"]
    assert ctrl_rank.candidates("flihgt", ["flight booked"]) in ([0], [])  # a swapped pair may be too far
    assert ctrl_rank.candidates("flights", texts)[:2] == [0, 2]
    assert ctrl_rank.candidates("frday flight", texts)[0] == 0
    assert ctrl_rank.candidates("zebra", texts) == []
    assert len(ctrl_rank.candidates("flight", [f"flight {i}" for i in range(90)])) == ctrl_rank.CANDIDATES


@pytest.mark.parametrize("secret", ["password: hunter2", "your code is 482913", "sk-or-v1-abcdefghijklmnop",
                                    "card 4111 1111 1111 1111", "https://example.com/hook/abc"])
def test_secrets_are_blanked_before_leaving(secret):
    sent = ctrl_rank.snippet("note", f"note {secret} end")
    assert "[hidden]" in sent and secret.split()[-1] not in sent


def jev(monkeypatch, chances, seen=None):
    key = type("K", (), {"openrouter_key": staticmethod(lambda d: "test-key")})
    monkeypatch.setattr(be, "_load_module", lambda name, *a: key if name == "vibi_gemini_ask" else None)

    def post(body, k):
        if seen is not None:
            seen.update(body=body, key=k)
        return {"answers": {"match": {"probabilities": chances}}}

    monkeypatch.setattr(ctrl_rank, "_post", post)


def test_jev_ranks_only_the_possible_entries(data, monkeypatch):
    monkeypatch.setattr(be, "todos", lambda d: [])
    monkeypatch.setattr(be, "messages", lambda d, **kw: [
        {"id": "c1", "ts": 2, "body": "flight prices went up"}, {"id": "c2", "ts": 1, "body": "unrelated words"}])
    seen = {}
    jev(monkeypatch, {"1": 0.2, "2": 0.79, "none": 0.01}, seen)
    found = ctrl_search.search(data, "flights")
    assert [(h["id"], h["meta"].split(" · ")[0]) for h in found["results"]] == [("c1", "Best match 79%"), ("n1", "Match 20%")]
    criteria = seen["body"]["questions"]["match"]["criteria"]
    assert set(criteria) == {"1", "2", "none"} and "unrelated" not in json.dumps(seen["body"])
    assert seen["body"]["model"] == "typesafe/jev-1.13" and seen["key"] == "test-key"


def test_without_jev_word_search_still_works(data, monkeypatch):
    monkeypatch.setattr(be, "_load_module", lambda *a: None)
    monkeypatch.setattr(be, "messages", lambda d, **kw: [])
    monkeypatch.setattr(be, "todos", lambda d: [])
    found = ctrl_search.search(data, "flights")
    assert [h["id"] for h in found["results"]] == ["n1"] and found["note"].startswith("Not ranked by Jev")


def test_the_tabs_are_merged_into_chat():
    html = UI.read_text()
    assert 'TABS=[["chat","Chat"],["components","Components"]]' in html and "loadTodos" not in html
    assert '{label:"Note (save)",value:"note"}' in html
