"""The notification collector copies a received WhatsApp photo or video so the chat can show it."""
import importlib.util
import sqlite3
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("collector", Path(__file__).resolve().parents[2] / "scripts" / "whatsapp_collector.py")
col = importlib.util.module_from_spec(spec)
spec.loader.exec_module(col)

NOW = 1_800_000_000.0


@pytest.fixture
def box(tmp_path):
    store, data = tmp_path / "store", tmp_path / "data"
    (store / "Message" / "Media").mkdir(parents=True)
    data.mkdir()
    db = sqlite3.connect(store / "ChatStorage.sqlite")
    db.executescript("""create table ZWACHATSESSION(Z_PK integer primary key, ZPARTNERNAME text);
        create table ZWAMESSAGE(Z_PK integer primary key, ZCHATSESSION int, ZISFROMME int, ZMESSAGETYPE int, ZMESSAGEDATE real);
        create table ZWAMEDIAITEM(Z_PK integer primary key, ZMESSAGE int, ZMEDIALOCALPATH text, ZTITLE text);""")
    db.execute("insert into ZWACHATSESSION values(1,'‎Sam')")

    def add(pk, name, kind=1, at=NOW, caption=None, mine=0, content=b"bytes"):
        rel = f"Media/{name}"
        if content is not None:
            (store / "Message" / rel).write_bytes(content)
        db.execute("insert into ZWAMESSAGE values(?,1,?,?,?)", (pk, mine, kind, at - col.APPLE_EPOCH))
        db.execute("insert into ZWAMEDIAITEM values(?,?,?,?)", (pk, pk, rel, caption))
        db.commit()

    return store, data, add


def note(body, ts=NOW, sender="Sam", group=""):
    return {"id": "1", "sender": sender, "group": group, "body": body, "ts": ts}


@pytest.mark.parametrize("body,kind,name", [("\U0001f4f7 Photo", 1, "a.jpg"), ("\U0001f3a5 Video (0:12)", 2, "a.mp4"),
                                            ("Look at this", 1, "a.jpg")])
def test_matching_media_is_copied(box, body, kind, name):
    store, data, add = box
    add(1, name, kind, NOW - 5, caption="Look at this" if body.startswith("Look") else None)
    msgs = [note(body)]
    col.attach_media(msgs, data, store)
    media = msgs[0]["media"]
    copy = data / media["file"]
    assert media["kind"] == ("video" if kind == 2 else "image") and copy.read_bytes() == b"bytes"
    assert copy.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("kwargs,body", [
    ({"at": NOW - 900}, "Photo"),                 # too old to be this notification
    ({"mine": 1}, "Photo"),                       # sent by the user
    ({}, "see you at 6"),                         # a text next to a photo
    ({"kind": 2, "name": "a.mp4"}, "Photo"),      # wrong kind
    ({"content": None}, "Photo"),                 # not downloaded yet
    ({"name": "../../../secret.jpg", "content": None}, "Photo"),
])
def test_nothing_is_copied_when_it_does_not_match(box, kwargs, body):
    store, data, add = box
    (store.parent / "secret.jpg").write_bytes(b"secret")
    add(1, kwargs.pop("name", "a.jpg"), **kwargs)
    msgs = [note(body)]
    col.attach_media(msgs, data, store)
    assert "media" not in msgs[0] and not list((data / "whatsapp_media").glob("*"))


def test_other_chat_does_not_match_and_one_file_is_claimed_once(box):
    store, data, add = box
    add(1, "a.jpg")
    msgs = [note("Photo", sender="Alex")]
    col.attach_media(msgs, data, store)
    assert "media" not in msgs[0]
    msgs = [note("Photo"), note("Photo", ts=NOW + 1)]
    col.attach_media(msgs, data, store)
    assert sum("media" in m for m in msgs) == 1


def test_old_copies_are_removed(box):
    store, data, add = box
    add(1, "a.jpg")
    msgs = [note("Photo")]
    col.attach_media(msgs, data, store)
    col.attach_media([], data, store)
    assert not list((data / "whatsapp_media").glob("*"))


def test_unreadable_store_leaves_notifications_alone(tmp_path):
    msgs = [note("Photo")]
    col.attach_media(msgs, tmp_path, tmp_path / "missing")
    assert msgs == [note("Photo")]
