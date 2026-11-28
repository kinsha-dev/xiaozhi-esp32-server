"""Saved notes, reminders and todos as Chat tab entries, and adding a note from the Chat tab."""
import json
import uuid
from datetime import datetime
from pathlib import Path

from mac_app import ctrl_backend as be


def _entry(kind, id_, ts, source, body, status, item=None):
    when = be._epoch(ts)
    return {"id": f"{kind}-{id_}", "source": source, "channel": kind, "direction": "to", "ts": when,
            "time": datetime.fromtimestamp(when).strftime("%b %d %H:%M") if when else "?", "status": status,
            "summary": body.split("\n")[0][:140], "body": body, "pair_id": "", "input": "", "recognized": None,
            "actions": [], "type": "todo" if kind == "todo" else "note", **({"item": item} if item else {})}


def rows(data_dir: Path) -> list:
    d, out = Path(data_dir), []
    for n in be._jsonl(d / "notes.jsonl"):
        if n.get("id") and n.get("text"):
            out.append(_entry("note", n["id"], n.get("ts"), "Note", n["text"], n.get("tag") or "saved",
                              {"kind": "note", "id": str(n["id"]), "text": n["text"]}))
    for r in be._jsonl(d / "reminders.jsonl"):
        if r.get("id") and r.get("text"):
            due = "done" if r.get("done") else f"due {r.get('due', '')}"
            out.append(_entry("reminder", r["id"], r.get("created"), "Reminder", r["text"], due))
    store = be._todo_store(d)
    for t in store.todos(d) if store else []:
        out.append(_entry("todo", t["id"], t.get("created"), "Todo", t["text"], "done" if t.get("done") else "open",
                          {"kind": "todo", "id": t["id"], "text": t["text"], "done": bool(t.get("done"))}))
    return out


def add_note(data_dir: Path, text: str) -> dict:
    text = str(text or "").strip()
    if not text:
        raise ValueError("empty note")
    if len(text) > be.CHAT_LIMIT:
        raise ValueError("note too long")
    row = {"id": uuid.uuid4().hex[:8], "ts": datetime.now().strftime("%Y-%m-%d %H:%M"), "tag": "", "text": text}
    d = Path(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    from mac_app import ctrl_search  # ctrl_search imports this module
    with ctrl_search._notes_lock(d), open(d / "notes.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row
