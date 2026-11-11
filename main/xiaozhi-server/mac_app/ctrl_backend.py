"""Vibi Ctrl data layer (no UI): history lists, component status, restart, rerun. Pure functions over data/ files."""
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

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
                                 " | ".join(x for x in (a.get("args"), a.get("result")) if x), bool(a.get("risky")),
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
