"""Voice and typed turns for the Chat timeline, merged with the message board rows. Read from the logs that already exist:

* data/history.jsonl: "command" (what Vibi heard or was sent), "reply" (what it answered, logged live by the server),
  "typed" (the panel marks what was typed, since the server logs typed and spoken commands alike)
* data/memory/conversations.jsonl: finished conversations, for replies from before "reply" rows existed
* data/outbox.jsonl: announcements and reminders the board leaves out

Every row gets "input": "voice", "typed" or "" (neither, or unknown).

Spoken turns are shown only when they were recognised and acted on:
* recognised: the server recorded that the speech went to the model or ran a tool ("llm_input" / tool action rows), or it
  is in a kept conversation. Speech ignored for not naming Vibi leaves only its "command" row.
* acted on: a tool ran for it, or it got a real reply. "Didn't catch that" style replies are not real replies.
* wake / bye chatter (nothing but greetings and Vibi's name, or the exit tool) is left out, with its replies.
Replies nobody asked for just then (a finished task's result) are kept unless they are a fallback or a bare greeting.
Typed turns are never filtered.
"""
import bisect
import functools
import json
import re
from datetime import datetime
from pathlib import Path

from core.utils import name_filter

WAKE_PROMPT = "Hey, hello there"  # what the server sends the model for a bare wake word
COMPUTER = "use the computer"
NOT_TOOLS = ("llm_input", "llm_prompt", "handle_exit_intent")  # action rows that are not "something was done"
REPLY_WAIT_S = 180  # a later reply is not an answer to the command
AWAY_NOTE = re.compile(r"\[While the user was away.*", re.S)
SMALL_TALK = {"hi", "hey", "hello", "ok", "okay", "yo", "we", "good", "morning", "afternoon", "evening", "night", "bye",
              "goodbye", "thanks", "thank", "you", "see", "ya", "that", "s", "all", "time", "flies"}
# what Vibi says when it could not make sense of the speech (no flag is recorded for this, so it is matched on wording)
FALLBACK = re.compile(
    r"^\W*(?:(?:sorry|hmm|okay)\W+)?(?:"
    r"(?:i )?didn.?t (?:quite )?(?:catch|get|hear|understand)"
    r"|that(?:.s| is| one.s| one is) (?:too |still )?garbled"
    r"|that(?: one| still)? (?:came through (?:garbled|unclear)|didn.?t come through)"
    r"|i.?m (?:still )?not following"
    r"|i.?m (?:still )?not sure (?=.*(?:say|repeat|do you (?:want|mean)|did you|you.?d? ?like|you mean|you.?re asking|caught))"
    r"|i.?ll leave it"
    r"|(?:could|can) you (?:say|repeat))", re.I | re.S)
GREETING = re.compile(r"^(?=.{0,160}$).*(?:what can i (?:do|help)|how can i help)", re.I | re.S)
ANNOUNCED = {"task": "announcement", "job_apply": "announcement", "": "announcement"}  # outbox sources the board skips


def _jsonl(path: Path) -> list:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


@functools.lru_cache(maxsize=16384)  # the panel polls every few seconds over the same lines
def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", str(text or "").lower())


def _same(a, b) -> bool:
    """The history log clips long text, so a prefix is enough."""
    a, b = _norm(a)[:100], _norm(b)[:100]
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


def _chatter(text: str) -> bool:
    """Nothing but a wake word, greeting or goodbye (or the server's own "end this conversation" prompt)."""
    text = AWAY_NOTE.sub("", str(text))
    if text.startswith("Please end this conversation"):
        return True
    return set(re.findall(r"[a-z]+", name_filter._NAME.sub(" ", text).lower())) <= SMALL_TALK


def _said(text) -> str:
    """The words of a log entry that may be the ASR's {"speaker", "content"} JSON."""
    text = str(text or "")
    if text.startswith("{"):
        found = re.search(r'"content"\s*:\s*"([^"]*)', text)
        if found:
            return found.group(1)
    return text


def _row(id_, ts, direction, body, input_, channel="", status="") -> dict:
    channel = channel or input_ or "voice"
    if channel in ("voice", "typed") and direction == "from" and _norm(body).startswith(_norm(COMPUTER)):
        source, channel = "Claude", "computer-use"
    else:
        source = "Vibi"
    return {"id": str(id_), "source": source, "channel": channel, "direction": direction, "ts": ts,
            "time": datetime.fromtimestamp(ts).strftime("%b %d %H:%M") if ts else "?", "status": status,
            "summary": body.split("\n")[0][:140], "body": body, "pair_id": "", "input": input_}


def _turns(data_dir: Path) -> list:
    """User turns and Vibi's replies."""
    history = sorted(_jsonl(data_dir / "history.jsonl"), key=lambda h: _num(h.get("ts")))
    users, replies, marks = [], [], []
    for h in history:
        kind, ts = h.get("kind"), _num(h.get("ts"))
        if kind == "command" and h.get("said"):
            users.append({"id": f"cmd-{h.get('id')}", "ts": ts, "text": h["said"], "input": "voice",
                          "heard": False, "tool": False, "chatter": h["said"] == WAKE_PROMPT or _chatter(h["said"])})
        elif kind == "action" and h.get("source") not in ("schedule", "task"):
            said = _said(h.get("said"))
            for u in reversed(users):  # the command this was done for
                if ts - u["ts"] > REPLY_WAIT_S:
                    break
                if _same(u["text"], said):
                    u["heard"] = True
                    u["tool"] = u["tool"] or h.get("name") not in NOT_TOOLS
                    u["chatter"] = u["chatter"] or h.get("name") == "handle_exit_intent"
                    break
        elif kind == "reply" and h.get("text"):
            replies.append({"id": f"say-{h.get('id')}", "ts": ts, "text": h["text"], "owner": None, "live": True})
        elif kind == "typed":
            text = h.get("text") or h.get("said") or ""
            if h.get("name") == "computer":
                text = f"Use the computer to {text}"
            marks.append({"id": f"typed-{h.get('id')}", "ts": ts, "text": text, "input": "typed"})

    # conversations that ended before replies were logged live
    live_from = min((r["ts"] for r in replies), default=float("inf"))
    free = sorted(users, key=lambda u: u["ts"])  # commands not yet matched to a conversation turn
    free_ts = [u["ts"] for u in free]
    for n, conv in enumerate(_jsonl(data_dir / "memory" / "conversations.jsonl")):
        try:
            end = datetime.strptime(str(conv.get("ts")), "%Y-%m-%d %H:%M").timestamp()
        except ValueError:
            continue
        if end + 60 > live_from:
            continue
        at, asked = end, None
        for i, turn in enumerate(conv.get("turns") or []):
            text, role = str(turn.get("text") or ""), turn.get("role")
            if not text:
                continue
            if role == "user":
                near = range(bisect.bisect_left(free_ts, end - 1800), bisect.bisect_right(free_ts, end + 120))
                hit = next((k for k in near if _same(free[k]["text"], text)), None)
                if hit is not None:
                    asked = free.pop(hit)
                    del free_ts[hit]
                    at = asked["ts"]
                else:
                    at = end + i * 0.001
                    asked = {"id": f"conv-{n}-{i}", "ts": at, "text": text, "input": "voice", "tool": False,
                             "chatter": text == WAKE_PROMPT or _chatter(text)}
                    users.append(asked)
                asked["heard"] = True  # only accepted speech reaches the dialogue
            elif role == "assistant":
                replies.append({"id": f"conv-{n}-{i}", "ts": at + i * 0.001, "text": text, "owner": asked, "live": False})

    # the panel's "typed" mark and the server's "command" row are one turn
    untouched = [u for u in users if u["input"] == "voice"]
    for mark in marks:
        hit = next((u for u in untouched if mark["ts"] - 60 <= u["ts"] <= mark["ts"] + 180 and _same(u["text"], mark["text"])), None)
        if hit:
            untouched.remove(hit)
            hit["input"] = "typed"
        else:
            users.append(mark)

    users.sort(key=lambda u: u["ts"])
    # a live reply answers the latest command that was recognised; ignored speech in between does not take it
    heard = [u for u in users if u["input"] == "typed" or u.get("heard") or u.get("chatter")]
    heard_at = [u["ts"] for u in heard]
    for r in replies:
        if r["live"]:
            k = bisect.bisect_right(heard_at, r["ts"])
            if k and r["ts"] - heard_at[k - 1] < REPLY_WAIT_S:
                r["owner"] = heard[k - 1]

    answered = {id(r["owner"]) for r in replies if r["owner"] and not FALLBACK.match(r["text"])}
    shown = {id(u) for u in users if u["input"] == "typed"
             or (u.get("heard") and not u.get("chatter") and (u.get("tool") or id(u) in answered))}
    rows = [_row(u["id"], u["ts"], "from", u["text"], u["input"]) for u in users if id(u) in shown]
    for r in replies:
        asked = r["owner"]
        typed = asked is not None and asked["input"] == "typed"
        if not typed and (FALLBACK.match(r["text"]) or (id(asked) not in shown if asked else GREETING.match(r["text"]))):
            continue
        rows.append(_row(r["id"], r["ts"], "to", r["text"], "typed" if typed else "voice"))
    return rows


def _announcements(data_dir: Path) -> list:
    rows = []
    for note in _jsonl(data_dir / "outbox.jsonl"):
        source = str(note.get("source") or "").strip().lower()
        if source not in ANNOUNCED or note.get("board") is False or not note.get("text"):
            continue
        channel = "reminder" if note.get("kind") == "reminder" else ANNOUNCED[source]
        rows.append(_row(f"out-{note.get('id')}", _num(note.get("ts")), "to", str(note["text"]), "", channel,
                         str(note.get("status") or "")))
    return rows


def _board_input(data_dir: Path, rows: list) -> list:
    claude = {str(r.get("id")): r.get("source") for r in _jsonl(data_dir / "to_claude.jsonl")}

    def of(r):
        rid = str(r.get("id"))
        if r.get("source") in ("Gemini", "Todo") or r.get("channel") == "typed":
            return "typed"  # only the panel files these
        if rid.endswith("-reply"):
            return "voice"  # a spoken answer to a message Vibi read out
        if r.get("channel") == "inbox" and rid in claude:
            return "typed" if claude[rid] == "Vibi Ctrl" else "voice"
        if rid.startswith("grok-") and rid.endswith("-req"):
            for folder in ("requests", "done", "failed"):
                try:
                    req = json.loads((data_dir / "grok" / folder / f"{rid[5:-4]}.json").read_text())
                except (OSError, ValueError):
                    continue
                return "typed" if req.get("reason") == "Vibi Ctrl chat" else ""
        return ""

    return [{**r, "input": of(r)} for r in rows]


def merge(data_dir: Path, board_rows: list) -> list:
    """Board rows, voice / typed turns and announcements, newest first."""
    data_dir = Path(data_dir)
    board = _board_input(data_dir, board_rows)
    turns, kept = _turns(data_dir), []
    for r in board:
        # a command typed in Stealth is filed on the board as "You: ... Vibi: ..."; show it once
        q = re.match(r"You: ([\s\S]*?)\n\nVibi: ([\s\S]*)$", r.get("body") or "") if r.get("channel") == "typed" else None
        if q:
            near = [t for t in turns if abs(t["ts"] - r["ts"]) < 1800]
            if any(t["direction"] == "to" and _same(t["body"], q[2]) for t in near):
                continue
            turns = [t for t in turns if not (t in near and t["direction"] == "from" and _same(t["body"], q[1]))]
        kept.append(r)
    rows = kept + turns + _announcements(data_dir)
    rows.sort(key=lambda r: -r["ts"])  # stable: board pairs keep their order
    return rows
