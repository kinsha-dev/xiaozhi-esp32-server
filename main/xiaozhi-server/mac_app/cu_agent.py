"""Runs the voice box's computer-use commands as a child of Vibi.app.

macOS attributes Screen Recording / Accessibility to the app that started the process. `bin/cu` is started by the
server (a bare Python), so the prompt said "Python". `server/cu_client.py` sends each command to this agent over a
private socket instead, and the agent runs it from inside Vibi.app, so the permission belongs to Vibi.
The safety rails stay in computer_use_exec.py; the agent only runs that one script with the given arguments.
"""
import json
import os
import socket
import subprocess
import threading
from pathlib import Path

PY = "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3"
EXEC = Path.home() / "esp/voice-note-box/server/computer_use_exec.py"
TIMEOUT_S = 300
MAX_ARGS = 50


def sock_path(data_dir) -> Path:
    return Path(data_dir) / "computer_use" / "agent.sock"


def run(request: dict) -> dict:
    argv = request.get("argv")
    if not isinstance(argv, list) or len(argv) > MAX_ARGS or not all(isinstance(a, str) for a in argv):
        return {"rc": 64, "out": "", "err": "bad request"}
    env = dict(os.environ)
    env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin"  # launchd's PATH lacks /usr/sbin (screencapture)
    box = request.get("data")
    if isinstance(box, str):
        env["VOICE_BOX_DATA"] = box
    try:
        r = subprocess.run([PY, str(EXEC), *argv], capture_output=True, text=True, timeout=TIMEOUT_S, env=env)
        return {"rc": r.returncode, "out": r.stdout, "err": r.stderr}
    except subprocess.TimeoutExpired:
        return {"rc": 124, "out": "", "err": "timed out"}
    except OSError as e:
        return {"rc": 127, "out": "", "err": str(e)}


def _serve_one(conn) -> None:
    with conn:
        conn.settimeout(10)
        buf = b""
        while not buf.endswith(b"\n") and len(buf) < 65536:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
        try:
            reply = run(json.loads(buf.decode() or "{}"))
        except ValueError:
            reply = {"rc": 64, "out": "", "err": "bad request"}
        conn.settimeout(None)
        conn.sendall((json.dumps(reply) + "\n").encode())


def start(data_dir) -> bool:
    """Listen on data/computer_use/agent.sock (owner only). Returns False when it cannot bind."""
    path = sock_path(data_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(path))
        os.chmod(path, 0o600)
        srv.listen(4)
    except OSError as e:
        print(f"[cu-agent] not started: {e}", flush=True)
        return False

    def loop():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=_serve_one, args=(conn,), daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()
    print(f"[cu-agent] computer use runs inside Vibi.app ({path})", flush=True)
    return True
