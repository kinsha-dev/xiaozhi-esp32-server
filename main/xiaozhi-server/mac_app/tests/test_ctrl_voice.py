"""Voice and typed turns, and Vibi's spoken replies, show in the Chat timeline (from the logs the server already keeps)."""
import json
import sys
import time
import types
from datetime import datetime
from pathlib import Path

from mac_app import ctrl_backend as be
from mac_app.ctrl import CtrlPanel

NOW = time.time()

HISTORY_STUB = """import json, time
def record(kind, name="", said="", source="voice", data_dir=None, text=None, **kw):
    row = {"id": f"t{time.time()}", "ts": time.time(), "kind": kind, "name": name, "said": said, "source": source, "text": text}
    with open(data_dir / "history.jsonl", "a") as f:
        f.write(json.dumps(row) + "\\n")
"""


def _setup(tmp_path, board=(), history=(), conversations=(), outbox=()):
    server = tmp_path / "server"
    (server / "plugin").mkdir(parents=True)
    (server / "message_board.py").write_text(f"def messages(data_dir, source=None):\n    return {list(board)!r}\n")
    (server / "plugin" / "history_log.py").write_text(HISTORY_STUB)
    data = tmp_path / "data"
    (data / "memory").mkdir(parents=True)
    for name, rows in (("history.jsonl", history), ("memory/conversations.jsonl", conversations), ("outbox.jsonl", outbox)):
        (data / name).write_text("".join(json.dumps(r) + "\n" for r in rows))
    return data


def _h(i, ago, kind, said="", text=None, name=""):
    return {"id": f"h{i}", "ts": NOW - ago, "kind": kind, "said": said, "text": text, "name": name}


def _sent(i, ago, said):  # the server records this only for speech it accepted and sent to the model
    return _h(f"llm{i}", ago, "action", said, name="llm_input")


def _conv(ago, *turns):
    return {"ts": datetime.fromtimestamp(NOW - ago).strftime("%Y-%m-%d %H:%M"), "session": f"s{ago}",
            "turns": [{"role": r, "text": t} for r, t in turns]}


def _view(data, **kw):  # oldest first, as the chat draws it
    return [(r["direction"], r["input"], r["body"]) for r in reversed(be.messages(data, **kw))]


def test_live_voice_turns_and_spoken_replies(tmp_path):
    data = _setup(tmp_path, history=[_h(1, 60, "command", "what time is it"), _sent(1, 60, "what time is it"), _h(2, 59, "reply", text="It is noon."),
                                     _h(3, 30, "action", "ignored"), _h(4, 20, "command", "Hey, hello there")])
    assert _view(data) == [("from", "voice", "what time is it"), ("to", "voice", "It is noon.")]
    assert {r["type"] for r in be.messages(data)} == {"vibi"}


def test_typed_commands_are_tagged_typed_with_their_reply(tmp_path):
    data = _setup(tmp_path, history=[
        _h(1, 90, "typed", "open mail", "open mail", "vibi"), _h(2, 89, "command", "open mail"), _sent(2, 89, "open mail"),
        _h(3, 88, "reply", text="Opening mail."),
        _h(4, 50, "typed", "play news", "play news", "computer"), _h(5, 49, "command", "Use the computer to play news"),
        _h(6, 20, "typed", "not logged yet", "not logged yet", "vibi")])
    assert _view(data) == [("from", "typed", "open mail"), ("to", "typed", "Opening mail."),
                           ("from", "typed", "Use the computer to play news"), ("from", "typed", "not logged yet")]
    assert [r["type"] for r in be.messages(data, input="typed")] == ["vibi", "computer", "vibi", "vibi"]
    assert be.messages(data, input="voice") == []


def test_finished_conversations_supply_replies_that_have_no_live_row(tmp_path):
    data = _setup(tmp_path, history=[_h(1, 400, "command", "open notes"), _h(2, 390, "command", "read messages")],
                  conversations=[_conv(300, ("user", "open notes"), ("assistant", "Opening notes."), ("user", "read messages"),
                                       ("assistant", "No new messages."))])
    assert [(d, b) for d, _, b in _view(data)] == [("from", "open notes"), ("to", "Opening notes."), ("from", "read messages"),
                                                   ("to", "No new messages.")]


def test_conversation_records_are_not_repeated_once_replies_are_live(tmp_path):
    data = _setup(tmp_path, history=[_h(1, 400, "command", "add two and two"), _sent(1, 400, "add two and two"), _h(2, 399, "reply", text="Four.")],
                  conversations=[_conv(300, ("user", "add two and two"), ("assistant", "Four."))])
    assert [b for *_, b in _view(data)] == ["add two and two", "Four."]


def test_announcements_and_reminders_and_stealth_rows(tmp_path):
    stealth = {"id": "s1", "source": "Vibi", "channel": "typed", "direction": "to", "ts": NOW - 10, "time": "t",
               "status": "done", "summary": "", "body": "You: ping\n\nVibi: pong", "pair_id": ""}
    data = _setup(tmp_path, board=[stealth],
                  history=[_h(1, 12, "typed", "ping", "ping", "vibi"), _h(2, 11, "command", "ping")],
                  outbox=[{"id": "o1", "ts": NOW - 100, "source": "task", "kind": "info", "text": "Morning news.", "status": "delivered"},
                          {"id": "o2", "ts": NOW - 90, "source": "", "kind": "reminder", "text": "Stand up.", "status": "pending"},
                          {"id": "o3", "ts": NOW - 80, "source": "Claude", "kind": "status", "text": "already on the board"}])
    rows = list(reversed(be.messages(data)))
    assert [(r["channel"], r["body"]) for r in rows] == [("announcement", "Morning news."), ("reminder", "Stand up."),
                                                         ("typed", "You: ping\n\nVibi: pong")]
    assert rows[-1]["input"] == "typed"


def test_panel_chat_rows_are_typed_and_voice_notes_to_claude_are_voice(tmp_path):
    def row(i, source, channel, direction):
        return {"id": i, "source": source, "channel": channel, "direction": direction, "ts": NOW, "time": "t",
                "status": "new", "summary": "x", "body": "x", "pair_id": ""}
    data = _setup(tmp_path, board=[row("a", "Claude", "inbox", "from"), row("b", "Claude", "inbox", "from"),
                                   row("grok-g1-req", "Grok Bot", "handoff", "from"), row("c-reply", "Claude", "outbox", "from"),
                                   row("d", "Claude", "outbox", "to")])
    (data / "to_claude.jsonl").write_text('{"id":"a","source":"Vibi Ctrl"}\n{"id":"b"}\n')
    (data / "grok" / "requests").mkdir(parents=True)
    (data / "grok" / "requests" / "g1.json").write_text('{"reason":"Vibi Ctrl chat"}')
    assert {r["id"]: r["input"] for r in be.messages(data)} == {"a": "typed", "b": "voice", "grok-g1-req": "typed",
                                                               "c-reply": "voice", "d": ""}


def test_panel_logs_what_you_type_to_vibi_and_computer_use(tmp_path):
    data = _setup(tmp_path)
    p = CtrlPanel(data, data, lambda text: True)
    for to in ("vibi", "computer"):
        assert p.handle("POST", "/api/chat", {"t": p.token}, {"to": to, "text": f"hello {to}"})[0] == 200
    assert [(r["type"], r["input"], r["body"]) for r in reversed(be.messages(data))] == [
        ("vibi", "typed", "hello vibi"), ("computer", "typed", "Use the computer to hello computer")]
    code, _, out = p.handle("GET", "/api/messages", {"t": p.token, "input": "voice"}, {})
    assert code == 200 and json.loads(out) == []


def test_server_logs_each_reply_put_into_the_dialogue(monkeypatch):
    from core.utils.dialogue import Dialogue, Message
    from core.utils import reply_log

    seen = []
    hl = types.SimpleNamespace(record_async=lambda *a, **k: seen.append((a, k)))
    pkg = types.ModuleType("plugins_func.functions")
    pkg.history_log = hl
    monkeypatch.setitem(sys.modules, "plugins_func.functions", pkg)
    d = Dialogue()
    d.on_put = reply_log.log_reply
    for m in (Message(role="user", content="hi"), Message(role="assistant", content="warm up", is_temporary=True),
              Message(role="assistant", content="再见"), Message(role="assistant", tool_calls=[1]),
              Message(role="assistant", content="Say confirm code 4821 to send.")):
        d.put(m)
    assert len(d.dialogue) == 5
    assert [(a, k["text"]) for a, k in seen] == [(("reply",), "Say confirm code [redacted] to send.")]


def test_only_recognised_and_acted_on_voice_commands_are_shown(tmp_path):
    away = "hi bibi [While the user was away 1 message arrived. Reply in English only.]"
    data = _setup(tmp_path, history=[
        _h(1, 900, "command", "We have a little handle for Olivia"),            # ignored speech: nothing recorded after it
        _h(2, 800, "command", away), _sent(2, 800, away),                      # wake chatter, even with a tool and a greeting
        _h(3, 799, "action", away, name="read_messages"), _h(4, 798, "reply", text="Hi Abi! How can I help?"),
        _h(5, 700, "command", "Val ke in group one"), _sent(5, 700, "Val ke in group one"),      # misheard
        _h(6, 699, "reply", text="That came through garbled. Could you say it again?"),
        _h(7, 600, "command", "Number two"), _sent(7, 600, "Number two"),      # sent to the model, nothing came back
        _h(8, 500, "command", "turn the TV up"), _h(9, 499, "action", "turn the TV up", name="tv_volume"),  # tool, no reply
        _h(10, 498, "command", "some background talk"),                        # does not steal the reply below
        _h(11, 400, "command", "open the pull request"), _sent(11, 400, "open the pull request"),
        _h(12, 399, "command", "more noise from the TV"), _h(13, 398, "reply", text="Opening it in your browser."),
        _h(14, 300, "command", "bye bibi"), _sent(14, 300, "bye bibi"), _h(15, 299, "action", "bye bibi", name="handle_exit_intent"),
        _h(16, 298, "reply", text="Bye for now."),
        _h(17, 200, "command", "what is two plus two"), _sent(17, 200, "what is two plus two"),
        _h(18, 199, "reply", text="I'm not sure, but I'd say four."),
        _h(19, 20, "reply", text="computer_use says: the video is playing.")])  # a result nobody asked for just now
    assert [b for *_, b in _view(data)] == [
        "turn the TV up", "open the pull request", "Opening it in your browser.", "what is two plus two",
        "I'm not sure, but I'd say four.", "computer_use says: the video is playing."]


def test_old_conversations_follow_the_same_rule(tmp_path):
    data = _setup(tmp_path, history=[_h(1, 400, "command", "TV noise")], conversations=[_conv(
        300, ("user", "hi bibi"), ("assistant", "Hey you. What can I do for you?"), ("user", "mumble mumble"),
        ("assistant", "Didn't catch that."), ("user", "play the news"), ("assistant", "Playing the news."),
        ("user", "thank you"), ("assistant", "Anytime."))])
    assert [b for *_, b in _view(data)] == ["play the news", "Playing the news."]


def test_typed_turns_are_never_filtered(tmp_path):
    data = _setup(tmp_path, history=[_h(1, 90, "typed", "hi bibi", "hi bibi", "vibi"), _h(2, 89, "command", "hi bibi"),
                                     _sent(2, 89, "hi bibi"), _h(3, 88, "reply", text="Hi! How can I help?")])
    assert _view(data) == [("from", "typed", "hi bibi"), ("to", "typed", "Hi! How can I help?")]
