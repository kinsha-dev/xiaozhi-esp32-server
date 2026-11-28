"""Search by meaning for the Chat tab, with an embedding model run by Ollama on this Mac (nothing leaves the machine).

Vectors are cached in data/search_vectors.db, keyed by a hash of model and text. Missing ones are made in the background,
so a first search never waits for the whole history; the caller is told how many are still to do.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

MODEL = "nomic-embed-text"
URL = "http://127.0.0.1:11434/api/embed"
CHARS = 2000  # of each entry
BATCH = 32
NOW = 64  # entries few enough to embed while the search waits
MIN_SCORE = 0.55
TOP = 15
_indexing = threading.Lock()
_started = False


class Unavailable(Exception):
    """Search by meaning cannot run; the message says what to do."""


def embed(texts: list) -> list:
    body = json.dumps({"model": MODEL, "input": texts}).encode()
    request = urllib.request.Request(os.environ.get("VIBI_EMBED_URL") or URL, data=body, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            vectors = json.loads(response.read())["embeddings"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise Unavailable(f"the search model is missing: run “ollama pull {MODEL}”")
        raise Unavailable(f"Ollama answered HTTP {e.code}")
    except (OSError, ValueError, KeyError):
        _start_ollama()
        raise Unavailable("Ollama is not answering: search again in a few seconds")
    if len(vectors) != len(texts):
        raise Unavailable("Ollama gave an unusable answer")
    return vectors


def _start_ollama():
    """Start Ollama once (it listens on this Mac only) when it is installed and not running."""
    global _started
    exe = shutil.which("ollama") or next((p for p in ("/opt/homebrew/bin/ollama", "/usr/local/bin/ollama") if Path(p).exists()), None)
    if _started or not exe or os.environ.get("VIBI_EMBED_URL"):
        return
    _started = True
    subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def _key(text: str) -> str:
    return hashlib.sha256(f"{MODEL}\0{text}".encode()).hexdigest()


def _open(data_dir):
    path = Path(data_dir) / "search_vectors.db"
    db = sqlite3.connect(path, timeout=10)
    path.chmod(0o600)
    db.execute("CREATE TABLE IF NOT EXISTS v (k TEXT PRIMARY KEY, v BLOB)")
    return db


def _store(data_dir, texts: list):
    import numpy as np

    vectors = embed([f"search_document: {t}" for t in texts])
    db = _open(data_dir)
    with db:
        db.executemany("INSERT OR REPLACE INTO v VALUES (?, ?)",
                       [(_key(t), np.asarray(v, np.float32).tobytes()) for t, v in zip(texts, vectors)])
    db.close()


def _index(data_dir, texts: list):
    try:
        for i in range(0, len(texts), BATCH):
            _store(data_dir, texts[i:i + BATCH])
    except (Unavailable, sqlite3.Error):
        pass  # the next search says why and tries again
    finally:
        _indexing.release()


def scores(data_dir: Path, query: str, texts: list):
    """({position in `texts`: similarity} for entries near `query`, how many entries are not indexed yet)."""
    import numpy as np

    cut = [str(t)[:CHARS] for t in texts]
    wanted = np.asarray(embed([f"search_query: {query}"])[0], np.float32)

    def cached():
        db = _open(data_dir)
        have = dict(db.execute("SELECT k, v FROM v"))
        db.close()
        return have

    have = cached()
    missing = list(dict.fromkeys(t for t in cut if t and _key(t) not in have))
    if missing and len(missing) <= NOW:
        _store(data_dir, missing)
        have, missing = cached(), []
    elif missing and _indexing.acquire(blocking=False):
        threading.Thread(target=_index, args=(data_dir, missing[::-1]), daemon=True, name="ctrl-index").start()
    near = {}
    for i, t in enumerate(cut):
        blob = have.get(_key(t))
        if blob:
            vector = np.frombuffer(blob, np.float32)
            if vector.shape == wanted.shape:
                score = float(np.dot(vector, wanted) / ((np.linalg.norm(vector) * np.linalg.norm(wanted)) or 1))
                if score >= MIN_SCORE:
                    near[i] = score
    return near, len(missing)
