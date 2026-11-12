"""Vibi Ctrl: control panel opened by Ctrl+I (global hotkey) or the menu item. A tiny local web server (127.0.0.1,
random port, per-run token) serves ctrl_ui/index.html and a JSON API; the panel opens in the default browser."""
import ctypes
import ctypes.util
import hmac
import json
import os
import secrets
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from mac_app import ctrl_backend as be

UI = Path(__file__).with_name("ctrl_ui") / "index.html"
VIBI_APP = Path.home() / "Applications/Vibi.app"
KEY_I, MOD_CTRL = 34, 0x1000  # kVK_ANSI_I, controlKey
ALT_MODS = 0x1000 | 0x800  # Ctrl+Option, a fallback when Ctrl+I is taken or unreliable


class CtrlPanel:
    def __init__(self, data_dir, server_dir, rerun, quit_app=lambda: None, set_muted=None):
        """rerun(text) -> bool: replays `text` as a fresh voice turn (so the usual spoken confirmation applies)."""
        self.data_dir, self.server_dir, self._rerun, self._quit = Path(data_dir), Path(server_dir), rerun, quit_app
        self._set_muted = set_muted
        self.token = secrets.token_urlsafe(16)
        self.httpd = None
        self.port = None
        self.tunnel_httpd = None
        self.tunnel_port = None

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
            elif path == "/api/style" and method == "GET":
                data = {"style": be.get_style(self.data_dir), "styles": list(be.STYLES)}
            elif path == "/api/style" and method == "POST":
                data = {"style": be.set_style(self.data_dir, str(body.get("style", "")))}
            elif path == "/api/mute" and method == "POST" and self._set_muted:
                self._set_muted(bool(body.get("on")))
                data = {"ok": True, "muted": bool(body.get("on"))}
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

    # ---- remote access (Cloudflare quick tunnel) -------------------------------------------------------------
    # A second listener, used only as the tunnel's origin. EVERY request must carry the persistent secret from
    # data/ctrl_token (X-Ctrl-Token header, or the cookie set by a one-time ?t= login); the local listener above is
    # untouched. Never log the secret or write it to history.
    def remote_token(self):
        f = self.data_dir / "ctrl_token"
        try:
            tok = f.read_text().strip()
            if len(tok) >= 20:
                return tok
        except OSError:
            pass
        tok = secrets.token_urlsafe(32)
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(tok)
        os.chmod(f, 0o600)
        return tok

    @staticmethod
    def _same(a, b):
        return hmac.compare_digest(str(a).encode(), str(b).encode())

    def handle_remote(self, method, path, query, headers, body=None):
        """Returns (status, content-type, bytes, extra_headers). Auth is checked before anything else."""
        tok = self.remote_token()
        cookie = SimpleCookie(headers.get("Cookie") or "")
        got = headers.get("X-Ctrl-Token") or (cookie["ctrl_auth"].value if "ctrl_auth" in cookie else "")
        if got and self._same(got, tok):
            q = {k: v for k, v in query.items() if k != "t"}
            q["t"] = self.token  # internal per-run token, never trusted from the client
            code, ctype, data = self.handle(method, path, q, body)
            return code, ctype, data, {"Cache-Control": "no-store"}
        if method == "GET" and path == "/" and query.get("t") and self._same(query["t"], tok):
            # one-time login: set the cookie, then redirect so the secret leaves the URL
            return 302, "text/plain", b"", {"Location": "/", "Set-Cookie": f"ctrl_auth={tok}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=2592000"}
        time.sleep(0.5)  # slow down guessing
        return 401, "application/json", b'{"error":"unauthorized"}', {}

    def start_remote(self):
        if self.tunnel_httpd:
            return self.tunnel_port
        panel = self

        class H(BaseHTTPRequestHandler):
            def _go(self, method):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                body = None
                if method == "POST":
                    n = int(self.headers.get("Content-Length") or 0)
                    try:
                        body = json.loads(self.rfile.read(min(n, 65536)) or b"{}")
                    except ValueError:
                        body = {}
                code, ctype, data, extra = panel.handle_remote(method, u.path, q, self.headers, body)
                self.send_response(code)
                self.send_header("Content-Type", ctype + "; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                for k, v in extra.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._go("GET")

            def do_POST(self):
                self._go("POST")

            def log_message(self, *a):
                pass

        self.remote_token()
        self.tunnel_httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.tunnel_port = self.tunnel_httpd.server_address[1]
        threading.Thread(target=self.tunnel_httpd.serve_forever, daemon=True).start()
        return self.tunnel_port

    def stop_remote(self):
        if self.tunnel_httpd:
            self.tunnel_httpd.shutdown()
            self.tunnel_httpd.server_close()
            self.tunnel_httpd, self.tunnel_port = None, None

    def url(self):
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    def open(self):
        if self.httpd is None:
            self.start()
        webbrowser.open(self.url())

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()


def register_hotkeys(callback, combos=(("Ctrl+I", MOD_CTRL), ("Ctrl+Option+I", ALT_MODS))):
    """Global hotkeys through Carbon RegisterEventHotKey (no Accessibility permission needed), one shared handler.
    Returns {name: armed}. Call on the main thread before the AppKit run loop starts."""
    result = {n: False for n, _ in combos}
    try:
        carbon = ctypes.CDLL(ctypes.util.find_library("Carbon"))
        EventTypeSpec = type("EventTypeSpec", (ctypes.Structure,), {"_fields_": [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]})
        HotKeyID = type("HotKeyID", (ctypes.Structure,), {"_fields_": [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]})
        HANDLER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        last = [0.0]

        @HANDLER
        def handler(_next, _event, _data):
            try:
                if time.monotonic() - last[0] > 0.5:  # one press, one open
                    last[0] = time.monotonic()
                    callback()
            except Exception:
                pass
            return 0

        register_hotkeys._keep = handler  # keep the ctypes callback alive
        spec = EventTypeSpec(0x6B657962, 5)  # 'keyb', kEventHotKeyPressed
        carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
        carbon.InstallEventHandler.argtypes = [ctypes.c_void_p, HANDLER, ctypes.c_uint32, ctypes.POINTER(EventTypeSpec), ctypes.c_void_p, ctypes.c_void_p]
        target = carbon.GetApplicationEventTarget()
        if carbon.InstallEventHandler(target, handler, 1, ctypes.byref(spec), None, None) != 0:
            return result
        carbon.RegisterEventHotKey.argtypes = [ctypes.c_uint32, ctypes.c_uint32, HotKeyID, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
        for n, (name, mods) in enumerate(combos, 1):
            ref = ctypes.c_void_p()
            result[name] = carbon.RegisterEventHotKey(KEY_I, mods, HotKeyID(0x76436C, n), target, 0, ctypes.byref(ref)) == 0
    except Exception:
        pass
    return result
