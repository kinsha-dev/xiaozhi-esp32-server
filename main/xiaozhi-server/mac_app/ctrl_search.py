"""Chat tab search over past chat entries, todos and saved notes, and editing a saved note or open or done todo."""
import contextlib
import fcntl
import json
from pathlib import Path

from mac_app import ctrl_backend as be

SCOPES = ("all", "chat", "todo", "note")
LIMIT = 50  # results per scope
NOTE_LIMIT = 40000  # an edited note must fit the remote panel's 64 KB request


def _hit(kind, id_, text, meta, ts=""):
    return {"kind": kind, "id": str(id_), "text": text, "meta": meta, "ts": str(ts)}


def search(data_dir: Path, query: str, scope: str = "all") -> list:
    """Newest first within chat, todos, then notes: entries holding every word of `query`, in any case."""
    words = str(query or "").casefold().split()
    if scope not in SCOPES:
        raise ValueError("unknown search scope")
    if not words:
        return []
    d, found = Path(data_dir), []
    has = lambda text: all(w in str(text).casefold() for w in words)
    if scope in ("all", "todo"):
        todos = [t for t in reversed(be.todos(d)) if has(t["text"])]
        found += [_hit("todo", t["id"], t["text"], f"Todo · {'done' if t.get('done') else 'open'} · {t.get('created', '')[:16]}")
                  for t in todos[:LIMIT]]
    if scope in ("all", "note"):
        notes = [n for n in reversed(list(be._jsonl(d / "notes.jsonl"))) if n.get("id") and has(n.get("text", ""))]
        found += [_hit("note", n["id"], n.get("text", ""),
                       " · ".join(x for x in ("Note", n.get("tag"), n.get("ts")) if x)) for n in notes[:LIMIT]]
    if scope in ("all", "chat"):
        rows = be.messages(d, limit=10**9, hours=0, action="all")
        rows = sorted((r for r in rows if has(r.get("body") or r.get("summary") or "")),
                      key=lambda r: -(r.get("ts") or 0))
        found += [_hit("chat", r["id"], r.get("body") or r.get("summary") or "", " · ".join(
            x for x in (r.get("category"), "from you" if r.get("direction") == "from" else "", r.get("time")) if x))
            for r in rows[:LIMIT]]
    return found


def edit(data_dir: Path, kind: str, id_: str, text: str, old: str) -> dict:
    """Replace the text of one note or todo. `old` is the text the editor started from: when it no longer matches, nothing
    is changed. ValueError for bad input or such a clash, KeyError for an unknown id."""
    text = str(text or "").strip()
    if not text:
        raise ValueError("the text is empty")

    def changed(row):
        if row.get("text") != old:
            raise ValueError("it changed meanwhile: search again")
        return {**row, "text": text}

    if kind == "todo":
        store = be._need_todo_store(data_dir)
        text = " ".join(text.split())
        if len(text) > store.LIMIT:
            raise ValueError("todo too long")
        return store._change(data_dir, id_, changed)
    if kind != "note":
        raise ValueError("only notes and todos can be edited")
    if len(text) > NOTE_LIMIT:
        raise ValueError("note too long")
    with _notes_lock(data_dir):
        path = Path(data_dir) / "notes.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").split("\n")
        except FileNotFoundError:
            raise KeyError(id_)
        for i, line in enumerate(lines):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and str(row.get("id")) == id_:
                new = changed(row)
                lines[i] = json.dumps(new)
                tmp = path.with_name("notes.edit.tmp")
                tmp.write_text("\n".join(lines), encoding="utf-8")
                tmp.chmod(path.stat().st_mode & 0o777)
                tmp.replace(path)
                return new
    raise KeyError(id_)


@contextlib.contextmanager
def _notes_lock(data_dir):
    """The lock the voice server's note tools take on data/notes.jsonl."""
    with open(Path(data_dir) / ".notes.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield
