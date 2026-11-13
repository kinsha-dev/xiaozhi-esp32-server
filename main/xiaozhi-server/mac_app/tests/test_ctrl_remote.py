import json
import os
import stat
import time
import urllib.error
import urllib.request

from mac_app.ctrl import CtrlPanel


def req(port, path, headers=None, method="GET", body=None):
    class NoRedir(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers or {}, method=method, data=body)
    try:
        resp = urllib.request.build_opener(NoRedir).open(r)
        return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def test_remote_requires_token(tmp_path):
    ran = []
    p = CtrlPanel(tmp_path, tmp_path, lambda t: ran.append(t) or True)
    port = p.start_remote()
    tok = (tmp_path / "ctrl_token").read_text()
    assert stat.S_IMODE(os.stat(tmp_path / "ctrl_token").st_mode) == 0o600
    try:
        assert req(port, "/")[0] == 401
        assert req(port, "/api/components")[0] == 401
        assert req(port, f"/api/components?t={p.token}")[0] == 401  # the per-run token is not enough
        assert req(port, "/api/rerun", method="POST", body=b'{"kind":"x","id":"y"}')[0] == 401
        assert req(port, "/api/history?kind=actions", {"X-Ctrl-Token": "wrong"})[0] == 401
        assert ran == []
        assert req(port, "/api/history?kind=actions", {"X-Ctrl-Token": tok})[0] == 200
        code, h, _ = req(port, f"/?t={tok}")  # one-time login: cookie + redirect to a clean URL
        assert code == 302 and h["Location"] == "/" and "ctrl_auth=" in h["Set-Cookie"] and tok not in h["Location"]
        code, _, html = req(port, "/", {"Cookie": f"ctrl_auth={tok}"})
        assert code == 200 and b"Vibi" in html
        code, _, data = req(port, "/api/rerun", {"Cookie": f"ctrl_auth={tok}"}, "POST", b'{"kind":"actions","id":"nope"}')
        assert code == 200 and json.loads(data)["ok"] is False
    finally:
        p.stop_remote()


def test_local_listener_unchanged(tmp_path):
    p = CtrlPanel(tmp_path, tmp_path, lambda t: True)
    p.start()
    try:
        assert req(p.port, "/api/history?kind=actions")[0] == 403
        assert req(p.port, f"/api/history?kind=actions&t={p.token}")[0] == 200
    finally:
        p.stop()


def test_remote_hardening(tmp_path):
    import http.client
    p = CtrlPanel(tmp_path, tmp_path, lambda t: True)
    port = p.start_remote()
    tok = (tmp_path / "ctrl_token").read_text()

    def r(m, path, h=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", port)
        c.request(m, path, body, h or {})
        x = c.getresponse()
        return x.status, dict(x.getheaders()), x.read()
    try:
        H = {"X-Ctrl-Token": tok}
        assert r("GET", "/api/history?kind=actions", {**H, "Host": "evil.com"})[0] == 421
        assert r("POST", "/api/style", {**H, "Origin": "https://evil.com"}, b'{"style":"plain"}')[0] == 403
        assert r("POST", "/api/style", {**H, "Origin": f"http://127.0.0.1:{port}"}, b'{"style":"plain"}')[0] == 200
        s, h, _ = r("GET", "/", H)
        assert h["Referrer-Policy"] == "no-referrer" and h["X-Frame-Options"] == "DENY"
        assert r("POST", "/api/style", H, b"x" * 70000)[0] == 413
        p._fails = [time.monotonic()] * 10
        assert r("GET", "/api/history?kind=actions", H)[0] == 429  # locked out even with the right token
        assert tok not in str(r("GET", "/", {"Host": "evil.com"})[1])
    finally:
        p.stop_remote()
