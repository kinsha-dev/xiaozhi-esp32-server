"""Vibi Ctrl: control panel opened by Ctrl+I (global hotkey) or the menu item. A tiny local web server (127.0.0.1,
random port, per-run token) serves ctrl_ui/index.html and a JSON API; the panel opens in the default browser."""
import ctypes
import ctypes.util
import json
import os
import secrets
import subprocess
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from mac_app import ctrl_backend as be

UI = Path(__file__).with_name("ctrl_ui") / "index.html"
VIBI_APP = Path.home() / "Applications/Vibi.app"
KEY_I, MOD_CTRL = 34, 0x1000  # kVK_ANSI_I, controlKey


class CtrlPanel:
    def __init__(self, data_dir, server_dir, rerun, quit_app=lambda: None):
        """rerun(text) -> bool: replays `text` as a fresh voice turn (so the usual spoken confirmation applies)."""
        self.data_dir, self.server_dir, self._rerun, self._quit = Path(data_dir), Path(server_dir), rerun, quit_app
        self.token = secrets.token_urlsafe(16)
        self.httpd = None
        self.port = None

    def handle(self, method, path, query, body=None):
        """Returns (status, content-type, bytes)."""
        if path == "/":
            if query.get("t") != self.token:
                return 403, "text/plain", b"forbidden"
            return 200, "text/html", UI.read_bytes().replace(b"__TOKEN__", self.token.encode())
        if query.get("t") != self.token:
            return 403, "application/json", b'{"error":"forbidden"}'
        try:
            if path == "/api/history" and method == "GET":
                data = be.history(self.data_dir, query.get("kind", "actions"))
            elif path == "/api/components" and method == "GET":
                data = be.components(self.data_dir, self.server_dir)
            elif path == "/api/restart" and method == "POST":
                data = be.restart(str(body.get("key", "")), relaunch=self._relaunch)
            elif path == "/api/rerun" and method == "POST":
                data = self.do_rerun(str(body.get("kind", "")), str(body.get("id", "")))
            else:
                return 404, "application/json", b'{"error":"not found"}'
        except Exception as e:
            return 500, "application/json", json.dumps({"error": str(e)[:200]}).encode()
        return 200, "application/json", json.dumps(data).encode()

    def do_rerun(self, kind, id_):
        item = be.find_rerun(self.data_dir, kind, id_)
        if not item:
            return {"ok": False, "error": "nothing to replay for this entry"}
        ok = self._rerun(item["text"])
        note = ("Replayed as a voice request: Vibi will ask you to confirm out loud before doing it."
                if item["risky"] else "Replayed as a voice request.")
        return {"ok": bool(ok), "risky": item["risky"], "note": note if ok else "Could not start a session (busy or muted)."}

    def _relaunch(self):
        subprocess.Popen(["sh", "-c", f"sleep 2; open '{VIBI_APP}'"], start_new_session=True)
        self._quit()

    def start(self):
        panel = self

        class H(BaseHTTPRequestHandler):
            def _go(self, method):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                body = None
                if method == "POST":
                    n = int(self.headers.get("Content-Length") or 0)
                    try:
                        body = json.loads(self.rfile.read(n) or b"{}")
                    except ValueError:
                        body = {}
                code, ctype, data = panel.handle(method, u.path, q, body)
                self.send_response(code)
                self.send_header("Content-Type", ctype + "; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._go("GET")

            def do_POST(self):
                self._go("POST")

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self):
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    def open(self):
        if self.httpd is None:
            self.start()
        webbrowser.open(self.url())

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()


def register_hotkey(callback):
    """Global Ctrl+I through Carbon RegisterEventHotKey (no Accessibility permission needed). Returns True when armed.
    Must be called from the main thread, before the AppKit run loop starts."""
    try:
        carbon = ctypes.CDLL(ctypes.util.find_library("Carbon"))
        EventTypeSpec = type("EventTypeSpec", (ctypes.Structure,), {"_fields_": [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]})
        HotKeyID = type("HotKeyID", (ctypes.Structure,), {"_fields_": [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]})
        HANDLER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)

        @HANDLER
        def handler(_next, _event, _data):
            try:
                callback()
            except Exception:
                pass
            return 0

        register_hotkey._keep = handler  # keep the ctypes callback alive
        spec = EventTypeSpec(0x6B657962, 5)  # 'keyb', kEventHotKeyPressed
        carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
        carbon.InstallEventHandler.argtypes = [ctypes.c_void_p, HANDLER, ctypes.c_uint32, ctypes.POINTER(EventTypeSpec), ctypes.c_void_p, ctypes.c_void_p]
        if carbon.InstallEventHandler(carbon.GetApplicationEventTarget(), handler, 1, ctypes.byref(spec), None, None) != 0:
            return False
        ref = ctypes.c_void_p()
        carbon.RegisterEventHotKey.argtypes = [ctypes.c_uint32, ctypes.c_uint32, HotKeyID, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
        rc = carbon.RegisterEventHotKey(KEY_I, MOD_CTRL, HotKeyID(0x76436C, 1), carbon.GetApplicationEventTarget(), 0, ctypes.byref(ref))
        return rc == 0
    except Exception:
        return False
