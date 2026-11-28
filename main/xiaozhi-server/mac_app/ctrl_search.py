"""Chat tab search over past chat entries, todos and saved notes, and editing a saved note or open or done todo."""
import contextlib
import fcntl
import json
from pathlib import Path

from mac_app import ctrl_backend as be
from mac_app import ctrl_rank

SCOPES = ("all", "chat", "todo", "note")
LIMIT = 50  # word matches per scope
MATCH = 0.05  # the least chance Jev must give an entry to list it as a match
NOTE_LIMIT = 40000  # an edited note must fit the remote panel's 64 KB request


def _hit(kind, id_, text, meta, ts=""):
    return {"kind": kind, "id": str(id_), "text": text, "meta": meta, "ts": str(ts)}


def _entries(data_dir: Path, scope: str) -> list:
    """(hit, searched text) of everything in `scope`: todos, notes, then chat, each newest first."""
    d, out = Path(data_dir), []
    if scope in ("all", "todo"):
        out += [(_hit("todo", t["id"], t["text"], f"Todo · {'done' if t.get('done') else 'open'} · {t.get('created', '')[:16]}"),
                 t["text"]) for t in reversed(be.todos(d))]
    if scope in ("all", "note"):
        out += [(_hit("note", n["id"], n.get("text", ""), " · ".join(x for x in ("Note", n.get("tag"), n.get("ts")) if x)),
                 n.get("text", "")) for n in reversed(list(be._jsonl(d / "notes.jsonl"))) if n.get("id")]
    if scope in ("all", "chat"):
        rows = be.messages(d, limit=10**9, hours=0, action="all")
        rows = sorted((r for r in rows if r.get("channel") not in ("note", "reminder") and not r.get("item")),
                      key=lambda r: -(r.get("ts") or 0))
        for r in rows:
            text = r.get("body") or r.get("summary") or ""
            meta = (r.get("category"), "from you" if r.get("direction") == "from" else "", r.get("time"))
            out.append((_hit("chat", r["id"], text, " · ".join(x for x in meta if x)), text))
    return out


def search(data_dir: Path, query: str, scope: str = "all") -> dict:
    """{"results", "note"}: Jev's pick of the possible entries first (those it gives a real chance, best first), then the
    other entries holding every word of `query`, newest first, then the near misses. "note" says why Jev was not used."""
    words = str(query or "").casefold().split()
    if scope not in SCOPES:
        raise ValueError("unknown search scope")
    if not words:
        return {"results": [], "note": ""}
    entries, exact, count = _entries(data_dir, scope), [], {}
    for i, (hit, text) in enumerate(entries):
        if all(w in str(text).casefold() for w in words) and count.get(hit["kind"], 0) < LIMIT:
            count[hit["kind"]] = count.get(hit["kind"], 0) + 1
            exact.append(i)
    possible = ctrl_rank.candidates(query, [text for _, text in entries])
    ranked, note = {}, ""
    try:
        chances = ctrl_rank.rank(data_dir, query.strip(), [entries[i][1] for i in possible])
        ranked = {possible[n]: p for n, p in chances.items() if p >= MATCH}
        if possible and not ranked:
            note = "Jev found no clear match; showing word and near matches."
    except ctrl_rank.Unavailable as e:
        note = f"Not ranked by Jev: {e}."

    def marked(i, label):
        hit = entries[i][0]
        return {**hit, "meta": f"{label} · {hit['meta']}"} if label else hit

    top = sorted(ranked, key=ranked.get, reverse=True)
    results = [marked(i, f"{'Best match' if n == 0 else 'Match'} {round(ranked[i] * 100)}%") for n, i in enumerate(top)]
    results += [marked(i, "") for i in exact if i not in ranked]
    results += [marked(i, "Near match") for i in possible if i not in ranked and i not in exact]
    return {"results": results, "note": note}


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
