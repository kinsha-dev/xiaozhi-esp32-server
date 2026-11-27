"""Vibi Ctrl data layer (no UI): history lists, component status, restart, rerun. Pure functions over data/ files."""
import base64
import binascii
import fcntl
import json
import os
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from mac_app import ctrl_voice

LABELS = {"server": "com.voicebox.server", "whisper": "com.voicebox.whisper", "macapp": "com.voicebox.macapp"}
ERR_LINE = re.compile(r"(ERROR|Traceback|Exception|CRITICAL|Error:)")
SECRET = re.compile(r"(sk-[A-Za-z0-9_-]{12,}|[A-Za-z0-9_-]{32,}|(?:password|token|key)\s*[=:]\s*\S+)", re.I)


def _jsonl(path: Path):
    try:
        for line in path.read_text().splitlines():
            try:
                yield json.loads(line)
            except ValueError:
                continue
    except OSError:
        return


def _epoch(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(str(v), fmt).timestamp()
        except ValueError:
            continue
    return 0.0


def _row(id_, ts, title, status="ok", detail="", risky=False, rerun=None, source=""):
    t = _epoch(ts)
    return {"id": id_, "ts": t, "time": datetime.fromtimestamp(t).strftime("%b %d %H:%M") if t else "?", "title": title,
            "status": status, "detail": detail, "risky": risky, "rerun": rerun, "source": source}


CONVERSATION_TYPES = ("claude", "grok", "gemini", "computer", "vibi", "todo")
_SOURCE_TYPES = {"Claude": "claude", "Grok Bot": "grok", "Gemini": "gemini", "Vibi": "vibi", "Todo": "todo",
                 "WhatsApp": "whatsapp", "Mail digest": "mail"}


def message_type(row: dict) -> str:
    """The Chat tab's type filter value for one board row."""
    source, channel = str(row.get("source") or ""), str(row.get("channel") or "")
    if channel == "computer-use":
        return "computer"
    if channel == "job-hunt":
        return "jobs"
    if source.startswith("Notifications"):
        return "notifications"
    return _SOURCE_TYPES.get(source, "other")


def _has_direction(row: dict, direction: str) -> bool:
    # a Gemini / Todo "Q: ... A: ..." row is both what was asked and the answer
    both = (row.get("source") in ("Gemini", "Todo") and str(row.get("body")).startswith("Q: ")) or (
        row.get("channel") == "typed" and str(row.get("body")).startswith("You: "))
    return row.get("direction") == direction or both


def messages(data_dir: Path, limit: int = 300, hours: float = 48, type: str = "all", direction: str = "all",
             input: str = "all") -> list:
    """Every conversation with outside apps (Grok Bot, Claude, phone apps, WhatsApp, job hunt), newest first, each with a
    category and a type. Only the last `hours` hours (0 = everything), filtered by `type` (a message_type value,
    "conversation" or "all") `direction` ("to" Vibi, "from" you, or "all") and `input` ("voice", "typed" or "all") before the limit. Voice and
    typed turns, spoken replies and announcements are merged in (ctrl_voice). Read through the voice box repo's message_board module, found next to the data dir."""
    import importlib.util

    mod_path = Path(os.environ.get("VIBI_MESSAGE_BOARD") or Path(data_dir).parent / "server" / "message_board.py")
    if not mod_path.exists():
        return []
    spec = importlib.util.spec_from_file_location("vibi_message_board", mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rows = ctrl_voice.merge(Path(data_dir), mod.messages(Path(data_dir)))
    if hours and hours > 0:
        cutoff = time.time() - hours * 3600
        rows = [r for r in rows if (r.get("ts") or 0) >= cutoff]
    rows = [{**r, "type": message_type(r)} for r in rows]
    if type != "all":
        wanted = CONVERSATION_TYPES if type == "conversation" else (type,)
        rows = [r for r in rows if r["type"] in wanted]
    if input != "all":
        rows = [r for r in rows if r["input"] == input]
    if direction != "all":
        rows = [r for r in rows if _has_direction(r, direction)]
    rows = rows[:limit]
    return [{**r, "category": r["source"] if r["source"].lower() == r["channel"].lower() else f"{r['source']} · {r['channel']}"} for r in rows]


CHAT_TARGETS = ("claude", "grok", "gemini")
CHAT_LIMIT = 4000
FILE_LIMIT = 5 * 1024 * 1024


def _load_module(name: str, data_dir: Path, env: str, rel: str):
    """A module of the voice box repo (next to the data dir), or None when it is not there."""
    import importlib.util

    mod_path = Path(os.environ.get(env) or Path(data_dir).parent / "server" / rel)
    if not mod_path.exists():
        return None
    spec = importlib.util.spec_from_file_location(name, mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def save_upload(data_dir: Path, name: str, b64: str) -> Path:
    """Store a file picked in the Chat tab under data/uploads/. Returns its path. ValueError if unusable."""
    try:
        raw = base64.b64decode(b64 or "", validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("bad file")
    if not raw:
        raise ValueError("empty file")
    if len(raw) > FILE_LIMIT:
        raise ValueError("file too large (5 MB max)")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(str(name or "file")).name).strip("._") or "file"
    dest = Path(data_dir) / "uploads" / f"{uuid.uuid4().hex[:6]}-{safe[:80]}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    return dest


def _ask_gemini_async(data_dir: Path, ask_mod, board_mod, text: str, path):
    """Answer on a thread (Gemini, OpenRouter if it fails) and file the result on the board."""
    label = f"{text}\n[file: {Path(path).name}]" if path else text

    def work():
        try:
            file = ask_mod.load_file(path) if path else None
            answer, via = ask_mod.answer(Path(data_dir), text, file)
            body, status = f"Q: {label}\n\nA: {answer}" + (" (via OpenRouter)" if via == "openrouter" else ""), "done"
        except (RuntimeError, ValueError) as e:
            body, status = f"Q: {label}\n\n{e}", "failed"
        try:
            board_mod.post_message(body, Path(data_dir), source="Gemini", channel="gemini", direction="to", status=status)
        except (OSError, ValueError):
            pass

    threading.Thread(target=work, daemon=True, name="ctrl-gemini").start()


def _grok_webhook():
    """(url, auth) for the Grok Bot's webhook: this process's environment, else the voice server's launchd job (the Mac app is
    not started by launchd, so it does not inherit GROK_WEBHOOK_*). Never logged."""
    url, auth = os.environ.get("GROK_WEBHOOK_URL", "").strip(), os.environ.get("GROK_WEBHOOK_AUTH", "").strip()
    if not url:
        try:
            import plistlib
            env = plistlib.loads((Path.home() / "Library/LaunchAgents/com.voicebox.server.plist").read_bytes())
            env = env.get("EnvironmentVariables") or {}
            url, auth = str(env.get("GROK_WEBHOOK_URL", "")).strip(), str(env.get("GROK_WEBHOOK_AUTH", "")).strip()
        except Exception:
            pass
    return url, auth


def _nudge_grok(mid: str) -> None:
    """Wake the Grok Bot for request `mid` (it is nudged by webhook, not by polling): POST {"id","kind","url"} like the voice path."""
    url, auth = _grok_webhook()
    if not url:
        return
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = auth if auth.lower().startswith("bearer ") else f"Bearer {auth}"

    def post():
        import ssl, urllib.request
        try:  # the python.org build has no root certificates of its own: use macOS's bundle
            ctx = ssl.create_default_context(cafile="/etc/ssl/cert.pem") if os.path.exists("/etc/ssl/cert.pem") else None
            urllib.request.urlopen(urllib.request.Request(
                url, data=json.dumps({"id": mid, "kind": "task", "url": ""}).encode(), headers=headers, method="POST"),
                timeout=10, context=ctx).close()
        except Exception:
            pass  # the request file is still there for the bot to poll

    threading.Thread(target=post, daemon=True, name="grok-nudge").start()


NOT_LISTENING = "Saved for Claude, but no Claude session is listening. It will be read when one starts listening."


def listener_in(commands) -> bool:
    """True when one of these process command lines is a `vibi listen` that takes "voice box" messages."""
    for line in commands:
        m = re.match(r"\S*[Pp]ython\S*\s+(?:-\S+\s+)*\S*vibi listen\b(.*)", line.strip())
        if not m:
            continue
        project = re.search(r"--project[ =](.*?)(?= --|$)", m.group(1))
        # no --project: it comes from the listener's folder, which we can't see here, so don't claim nobody is there
        if not project or {"voice", "box"} <= set(re.findall(r"[a-z0-9]+", project.group(1).lower())):
            return True
    return False


def claude_listening() -> bool:
    try:
        out = subprocess.run(["ps", "-axo", "command="], capture_output=True, text=True, timeout=5, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return True  # can't tell: no warning
    return listener_in(out.splitlines())


def chat(data_dir: Path, to: str, text: str, file: dict = None) -> dict:
    """Send a typed message from the Chat tab: to Claude (the voice box inbox the listener reads), to the Grok Bot
    (a `task` request in data/grok/requests) or to Gemini (answered in the background, OpenRouter if Gemini fails; the answer
    is posted on the board). Same file contracts the voice path uses, so replies show up in Chat. `file` is
    {"name", "data": base64}: saved under data/uploads/ and attached (Gemini reads it; Claude and Grok get its path)."""
    to, text = (to or "").lower(), (text or "").strip()
    if to not in CHAT_TARGETS:
        raise ValueError("to must be claude, grok or gemini")
    if not text:
        raise ValueError("empty message")
    if len(text) > CHAT_LIMIT:
        raise ValueError("message too long")
    d, mid, now = Path(data_dir), uuid.uuid4().hex[:6], time.time()
    d.mkdir(parents=True, exist_ok=True)
    path = save_upload(d, file.get("name"), file.get("data")) if file else None
    if to == "gemini":
        ask_mod = _load_module("vibi_gemini_ask", d, "VIBI_GEMINI_ASK", "gemini_ask.py")
        board_mod = _load_module("vibi_message_board", d, "VIBI_MESSAGE_BOARD", "message_board.py")
        if ask_mod is None or board_mod is None:
            raise ValueError("Gemini is not available: update the voice note box repo")
        if not (ask_mod.gemini_key(d) or ask_mod.openrouter_key(d)):
            raise ValueError(ask_mod.NOT_SET_UP)
        _ask_gemini_async(d, ask_mod, board_mod, text, path)
        return {"id": mid, "to": to}
    if path:
        text += f"\n[attached file: {path}]"
    if to == "claude":
        row = {"id": mid, "ts": datetime.fromtimestamp(now).strftime("%Y-%m-%dT%H:%M:%S"), "text": text,
               "status": "new", "project": "voice box", "source": "Vibi Ctrl"}
        with open(d / ".to_claude.lock", "w") as lock:  # the lock vibi and the server use for this file
            fcntl.flock(lock, fcntl.LOCK_EX)
            with open(d / "to_claude.jsonl", "a") as f:
                f.write(json.dumps(row) + "\n")
        if not claude_listening():
            return {"id": mid, "to": to, "note": NOT_LISTENING}
    else:
        req = d / "grok" / "requests"
        req.mkdir(parents=True, exist_ok=True)
        tmp = req / f".{mid}.tmp"
        tmp.write_text(json.dumps({"id": mid, "kind": "task", "url": "", "prompt": text,
                                   "created": datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S"),
                                   "reason": "Vibi Ctrl chat", **({"file": str(path)} if path else {})}))
        tmp.replace(req / f"{mid}.json")
        _nudge_grok(mid)
    return {"id": mid, "to": to}


# --- todos (shared with the voice tools through data/todos.jsonl) ----------------------------------
def note_typed(data_dir: Path, to: str, text: str):
    """Mark in the history log that this command came from the panel; the server logs it like a spoken one."""
    try:
        log = _load_module("vibi_history_log", data_dir, "VIBI_HISTORY_LOG", "plugin/history_log.py")
        if log is not None:
            log.record("typed", name=to, said=text, source="typed", data_dir=Path(data_dir), text=text)
    except Exception:
        pass  # only the voice / typed label is lost


def _todo_store(data_dir: Path):
    return _load_module("vibi_todo_store", data_dir, "VIBI_TODO_STORE", "todo_store.py")


def _need_todo_store(data_dir: Path):
    store = _todo_store(data_dir)
    if store is None:
        raise ValueError("Todos are not available: update the voice note box repo")
    return store


def todos(data_dir: Path) -> list:
    return _need_todo_store(data_dir).todos(data_dir)


def todo_op(data_dir: Path, op: str, text: str = "", id_: str = "") -> dict:
    """One change from the Todo tab. ValueError for bad input, KeyError for an unknown id."""
    store = _need_todo_store(data_dir)
    if op == "add":
        return store.add(data_dir, text, source="panel")
    if op in ("done", "reopen"):
        return store.set_done(data_dir, id_, op == "done")
    if op == "delete":
        return store.delete(data_dir, id_)
    raise ValueError("unknown todo operation")


def todo_chat(data_dir: Path, to: str, text: str, file=None):
    """A Chat tab message that is for the todo list: anything sent to "Todo list", a todo command typed to Vibi ("add a
    todo ...") or "todo: ..." to Claude / Grok / Gemini. Handled here (no agent involved) and the reply is filed on the
    board. Returns None for every other message."""
    to, text = (to or "").lower(), (text or "").strip()
    if to == "todo":
        store = _need_todo_store(data_dir)
        if file:
            raise ValueError("files can't be added to a todo")
        if not text or len(text) > CHAT_LIMIT:
            raise ValueError("empty or too long")
        command = store.parse_command(text) or ("add", text)
    else:
        explicit = to in CHAT_TARGETS and re.match(r"to-?do\s*:", text, re.I)
        if file or not (to == "vibi" or explicit):
            return None
        store = _todo_store(data_dir)
        command = store.parse_command(text) if store else None
        if command is None:
            return None
    reply = store.run_command(data_dir, command, source="chat")
    board = _load_module("vibi_message_board", data_dir, "VIBI_MESSAGE_BOARD", "message_board.py")
    if board is not None:
        try:
            board.post_message(f"Q: {text}\n\nA: {reply}", Path(data_dir), source="Todo", channel="todo")
        except (OSError, ValueError):
            pass  # the todo is saved; only the chat history line is missing
    return {"id": "", "to": "todo", "note": reply}


def history(data_dir: Path, kind: str, limit: int = 100) -> list:
    d = Path(data_dir)
    rows = []
    if kind == "notes":
        for n in _jsonl(d / "notes.jsonl"):
            rows.append(_row(f"n{n.get('id')}", n.get("ts"), n.get("text", ""), "saved", n.get("tag", ""), source="note"))
        for r in _jsonl(d / "reminders.jsonl"):
            rows.append(_row(f"r{r.get('id')}", r.get("created"), r.get("text", ""), "done" if r.get("done") else "pending",
                             f"due {r.get('due', '')}", source="reminder"))
    elif kind == "actions":
        for a in _jsonl(d / "history.jsonl"):
            if a.get("kind") == "action":
                said = a.get("said") or ""
                rows.append(_row(a["id"], a.get("ts"), a.get("name", ""), a.get("status", "ok"),
                                 " | ".join(x for x in (a.get("args"), a.get("result"), a.get("text")) if x), bool(a.get("risky")),
                                 said or None, a.get("source", "")))
    elif kind == "commands":
        for c in _jsonl(d / "history.jsonl"):
            if c.get("kind") == "command":
                rows.append(_row(c["id"], c.get("ts"), c.get("said", ""), "heard", "", False, c.get("said") or None, "voice"))
        for m in _jsonl(d / "to_claude.jsonl"):
            rows.append(_row(f"i{m.get('id')}", m.get("ts"), m.get("text", ""), m.get("status", ""), "", False, None, "inbox"))
    else:
        raise ValueError(kind)
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows[:limit]


def find_rerun(data_dir: Path, kind: str, id_: str):
    """The spoken text to replay for an entry, plus whether it is risky; None when it cannot be rerun."""
    for r in history(data_dir, kind, limit=100000):
        if r["id"] == id_ and r["rerun"]:
            return {"text": r["rerun"], "risky": r["risky"]}
    return None


# --- voice style (shared with the server through data/voice_style) -------------------------------
STYLES = ("flirty", "plain")


def get_style(data_dir: Path) -> str:
    s = _read(Path(data_dir) / "voice_style").lower()
    return s if s in STYLES else "flirty"


def set_style(data_dir: Path, style: str) -> str:
    style = (style or "").strip().lower()
    if style not in STYLES:
        raise ValueError("style must be flirty or plain")
    (Path(data_dir) / "voice_style").write_text(style)
    return style


# --- components --------------------------------------------------------------------------------
def _run(cmd, timeout=5) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def launchd_status(label: str, uid: int = None, run=_run) -> dict:
    out = run(["launchctl", "print", f"gui/{uid if uid is not None else os.getuid()}/{label}"])
    if not out:
        return {"running": False, "pid": None, "detail": "not loaded"}
    pid = re.search(r"^\s*pid = (\d+)", out, re.M)
    state = re.search(r"^\s*state = (\w+)", out, re.M)
    code = re.search(r"last exit code = (-?\d+)", out)
    return {"running": bool(pid), "pid": int(pid.group(1)) if pid else None,
            "detail": (state.group(1) if state else "") + (f", last exit {code.group(1)}" if code and not pid else "")}


def last_error(log: Path, tail_bytes: int = 200_000) -> str:
    try:
        with open(log, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - tail_bytes))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        if ERR_LINE.search(line):
            return SECRET.sub("***", " ".join(line.split()))[:200]
    return ""


def _read(p: Path) -> str:
    try:
        return p.read_text().strip()
    except OSError:
        return ""


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def components(data_dir: Path, server_dir: Path, status=launchd_status, now=time.time) -> list:
    d = Path(data_dir)
    out = []

    def add(key, name, running, pid="", detail="", error="", restart=None):
        out.append({"key": key, "name": name, "running": running, "pid": pid, "detail": detail, "error": error,
                    "restart": restart})

    for key, name, logf in (("server", "Voice server", d / "logs" / "server.log"), ("whisper", "Whisper", d / "logs" / "whisper.log")):
        s = status(LABELS[key])
        add(key, name, s["running"], s["pid"], s["detail"], last_error(logf), LABELS[key])
    add("macapp", "Mac app", True, os.getpid(), "this app", last_error(Path(server_dir) / "data" / "mac_app.log"), "macapp")
    # board link
    link = _read(d / "board_link") or "auto"
    hb = _read(d / "board_heartbeat")
    age = now() - float(hb) if hb.replace(".", "", 1).isdigit() else None
    silent = _read(d / "silent_until")
    ble = _read(d / "board_ble")
    online = age is not None and age < 300
    parts = [f"link: {link}", "BLE linked" if ble and _alive(ble) else "Wi-Fi/auto",
             f"heartbeat {int(age)}s ago" if age is not None else "no heartbeat"]
    muted = (d / "muted").exists()
    if muted:
        parts.insert(0, "MUTED (board and Mac are off)")
    if silent and silent.replace(".", "", 1).isdigit() and float(silent) > now():
        parts.append("SILENT until " + datetime.fromtimestamp(float(silent)).strftime("%H:%M"))
    add("board", "Board link", online, "", ", ".join(parts))
    out[-1]["muted"] = muted
    # notification collector: runs inside the voice server; status.json holds its last problem
    st = {}
    try:
        st = json.loads(_read(d / "notifications" / "status.json") or "{}")
    except ValueError:
        pass
    add("notif", "Notification collector", bool(out[0]["running"]) and not st.get("error"), out[0]["pid"],
        "runs inside the voice server", str(st.get("error") or st.get("problem") or "")[:200], LABELS["server"])
    topic = _read(d / "ntfy_topic")
    add("ntfy", "ntfy topic", bool(topic), "", topic or "not set")
    tv = _read(d / "tv_mac.txt")
    add("tv", "TV", bool(tv), "", f"TV {tv}" if tv else "not configured")
    cu = d / "computer_use"
    busy = (cu / "state.json").exists()
    last = {}
    try:
        last = json.loads(_read(cu / "last_run.json") or "{}")
    except ValueError:
        pass
    add("cu", "Computer use", True, "", "RUNNING a task" if busy else ("idle; last: " + str(last.get("task", "none"))[:60]))
    return out


def _kick(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    return r.returncode, (r.stderr or "").strip()[:200]


def restart(key: str, uid: int = None, kick=_kick, status=launchd_status, relaunch=None) -> dict:
    """launchctl kickstart -k gui/<uid>/<label>. The Mac app restarts itself, so that one is kicked in the background
    (or Vibi.app is relaunched through `relaunch` when it is not a loaded launchd job, e.g. started from the Dock)."""
    label = LABELS.get(key) or (key if key in LABELS.values() else None)
    if not label:
        return {"ok": False, "error": "unknown component"}
    u = uid if uid is not None else os.getuid()
    cmd = ["launchctl", "kickstart", "-k", f"gui/{u}/{label}"]
    if label == LABELS["macapp"]:
        # kickstart only re-runs `open -a Vibi.app`, which does nothing while the app is running: quit it, then reopen
        app = Path.home() / "Applications/Vibi.app"
        subprocess.Popen(["sh", "-c", f"sleep 1; pkill -f '[m]ac_app'; pkill -x Vibi; sleep 2; open '{app}'"], start_new_session=True)
        return {"ok": True, "note": "restarting the Mac app in 3 s"}
    rc, err = kick(cmd)
    return {"ok": rc == 0, "error": err}
