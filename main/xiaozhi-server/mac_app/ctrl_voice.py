"""Voice and typed turns for the Chat timeline, merged with the message board rows. Read from the logs that already exist:

* data/history.jsonl: "command" (what Vibi heard or was sent), "reply" (what it answered, logged live by the server),
  "typed" (the panel marks what was typed, since the server logs typed and spoken commands alike)
* data/memory/conversations.jsonl: finished conversations, for replies from before "reply" rows existed
* data/outbox.jsonl: announcements and reminders the board leaves out

Every row gets "input": "voice", "typed" or "" (neither, or unknown).
"""
import bisect
import functools
import json
import re
from datetime import datetime
from pathlib import Path

WAKE_PROMPT = "Hey, hello there"  # what the server sends the model for a bare wake word
COMPUTER = "use the computer"
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
    history = _jsonl(data_dir / "history.jsonl")
    users, replies, marks = [], [], []
    for h in history:
        kind, ts = h.get("kind"), _num(h.get("ts"))
        if kind == "command" and h.get("said") and h["said"] != WAKE_PROMPT:
            users.append({"id": f"cmd-{h.get('id')}", "ts": ts, "text": h["said"], "input": "voice"})
        elif kind == "reply" and h.get("text"):
            replies.append({"id": f"say-{h.get('id')}", "ts": ts, "text": h["text"]})
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
        at = end
        for i, turn in enumerate(conv.get("turns") or []):
            text, role = str(turn.get("text") or ""), turn.get("role")
            if not text or text == WAKE_PROMPT:
                continue
            if role == "user":
                near = range(bisect.bisect_left(free_ts, end - 1800), bisect.bisect_right(free_ts, end + 120))
                hit = next((k for k in near if _same(free[k]["text"], text)), None)
                if hit is not None:
                    at = free.pop(hit)["ts"]
                    del free_ts[hit]
                else:
                    at = end + i * 0.001
                    users.append({"id": f"conv-{n}-{i}", "ts": at, "text": text, "input": "voice"})
            elif role == "assistant":
                replies.append({"id": f"conv-{n}-{i}", "ts": at + i * 0.001, "text": text})

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
    rows = [_row(u["id"], u["ts"], "from", u["text"], u["input"]) for u in users]
    asked_at = [u["ts"] for u in users]
    for r in replies:
        k = bisect.bisect_right(asked_at, r["ts"])
        asked = users[k - 1] if k else None
        typed = asked and asked["input"] == "typed" and r["ts"] - asked["ts"] < 600
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
