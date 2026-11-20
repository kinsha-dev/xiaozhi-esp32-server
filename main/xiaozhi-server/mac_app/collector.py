"""Background helpers that run as children of Vibi (no separate launch agents / background items):
the WhatsApp/notification collector and the message-board webhook (127.0.0.1:8766).

Each is restarted after a pause if it exits and stopped with the app. The collector needs Full Disk Access for
Vibi.app (the child inherits it)."""
import os
import subprocess
import sys
import threading
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "whatsapp_collector.py"
VOICE_BOX = Path(os.environ.get("VOICE_BOX_ROOT", Path.home() / "esp" / "voice-note-box"))
BOARD = VOICE_BOX / "bin" / "vibi-board"


class ChildService:
    def __init__(self, name, argv):
        self.name = name
        self.argv = argv
        self._stop = threading.Event()
        self._proc = None

    def start(self):
        threading.Thread(target=self._run, name=self.name, daemon=True).start()

    def stop(self):
        self._stop.set()
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()

    def _run(self):
        while not self._stop.is_set():
            try:
                self._proc = subprocess.Popen(self.argv)
                code = self._proc.wait()
                print(f"[{self.name}] exited {code}; restarting", flush=True)
            except Exception as exc:
                print(f"[{self.name}] {type(exc).__name__}: {exc}", flush=True)
            self._stop.wait(15)


def CollectorService():
    return ChildService("collector", [sys.executable, str(SCRIPT)])


def BoardService():
    return ChildService("board", [sys.executable, str(BOARD), "serve", "--port", "8766"])
