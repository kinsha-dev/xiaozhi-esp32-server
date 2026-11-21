import hashlib
import http.client
import json
import os
import stat
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from mac_app.ctrl import CtrlPanel
from mac_app.ctrl_passkey import b64u, unb64u

HOST = "abc-def.trycloudflare.com"


class Authenticator:
    """Software passkey: ES256, user verification on (flags UP|UV), none attestation."""
    def __init__(self, uv=True):
        self.key, self.cid, self.count, self.uv = ec.generate_private_key(ec.SECP256R1()), os.urandom(16), 0, uv

    def _flags(self, extra=0):
        return (0x01 | (0x04 if self.uv else 0)) | extra

    def create(self, options, origin):
        o = options["options"]
        cd = json.dumps({"type": "webauthn.create", "challenge": o["challenge"], "origin": origin}).encode()
        n = self.key.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")})
        ad = hashlib.sha256(o["rp"]["id"].encode()).digest() + bytes([self._flags(0x40)]) + struct.pack(">I", 0) + bytes(16) \
            + struct.pack(">H", len(self.cid)) + self.cid + cose
        return {"cid": options["cid"], "name": "test", "credential": {"id": b64u(self.cid), "rawId": b64u(self.cid), "type": "public-key", "clientExtensionResults": {},
                "response": {"clientDataJSON": b64u(cd), "attestationObject": b64u(cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": ad}))}}}

    def get(self, options, origin, rp_id=HOST):
        o = options["options"]
        cd = json.dumps({"type": "webauthn.get", "challenge": o["challenge"], "origin": origin}).encode()
        self.count += 1
        ad = hashlib.sha256(rp_id.encode()).digest() + bytes([self._flags()]) + struct.pack(">I", self.count)
        sig = self.key.sign(ad + hashlib.sha256(cd).digest(), ec.ECDSA(hashes.SHA256()))
        return {"cid": options["cid"], "credential": {"id": b64u(self.cid), "rawId": b64u(self.cid), "type": "public-key", "clientExtensionResults": {},
                "response": {"authenticatorData": b64u(ad), "clientDataJSON": b64u(cd), "signature": b64u(sig), "userHandle": None}}}


class Client:
    def __init__(self, port, host=HOST):
        self.port, self.host, self.cookie = port, host, None

    def call(self, method, path, body=None, headers=None, origin=True):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        h = {"Host": self.host, **(headers or {})}
        if origin and method == "POST":
            h["Origin"] = origin if isinstance(origin, str) else f"https://{self.host}"
        if self.cookie:
            h["Cookie"] = self.cookie
        data = json.dumps(body).encode() if body is not None else None
        c.request(method, path, data, h)
        r = c.getresponse()
        raw = r.read()
        sc = r.getheader("Set-Cookie")
        if sc and sc.startswith("ctrl_auth="):
            self.cookie = sc.split(";")[0]
        try:
            return r.status, json.loads(raw), r
        except ValueError:
            return r.status, raw, r


def setup(tmp_path):
    p = CtrlPanel(tmp_path, tmp_path, lambda t: True)
    port = p.start_remote()
    tok = (tmp_path / "ctrl_token").read_text()
    return p, port, tok


def register(owner, auth):
    st, opts, _ = owner.call("POST", "/api/passkey/register/options")
    assert st == 200 and opts["options"]["rp"]["id"] == HOST
    assert opts["options"]["authenticatorSelection"]["userVerification"] == "required"
    return owner.call("POST", "/api/passkey/register/verify", auth.create(opts, f"https://{HOST}"))


def login(c, auth, **kw):
    st, opts, _ = c.call("POST", "/api/passkey/login/options")
    assert st == 200
    return c.call("POST", "/api/passkey/login/verify", auth.get(opts, f"https://{HOST}", **kw))


def test_register_then_login_with_passkey(tmp_path):
    p, port, tok = setup(tmp_path)
    try:
        owner, auth = Client(port), Authenticator()
        assert owner.call("POST", "/api/passkey/register/options")[0] == 401  # registering needs an authenticated session
        assert owner.call("GET", "/?t=" + tok, headers={})[0] == 302  # token link = bootstrap
        assert owner.call("GET", "/api/passkey/status")[1] == {"count": 0, "token_login": True, "rp_id": HOST}
        assert register(owner, auth)[0] == 200
        f = tmp_path / "ctrl_passkeys.json"
        assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
        stored = f.read_text()
        assert tok not in stored and "private" not in stored
        assert owner.call("GET", "/api/passkey/status")[1]["count"] == 1

        fresh = Client(port)  # a new browser: no cookie, only the passkey
        assert fresh.call("GET", "/api/history?kind=actions")[0] == 401
        st, page, _ = fresh.call("GET", "/")
        assert st == 401 and b"Sign in with passkey" in page and tok.encode() not in page
        assert login(fresh, auth)[0] == 200
        assert fresh.cookie.startswith("ctrl_auth=p.") and tok not in fresh.cookie
        st, page, r = fresh.call("GET", "/")
        assert st == 200 and b"/passkey.js" in page
        assert fresh.call("GET", "/api/history?kind=actions")[0] == 200
        assert json.loads(f.read_text())["credentials"][0]["sign_count"] == 1  # counter persisted
    finally:
        p.stop_remote()


def test_login_rejections(tmp_path):
    p, port, tok = setup(tmp_path)
    try:
        owner, auth = Client(port), Authenticator()
        owner.call("GET", "/?t=" + tok)
        register(owner, auth)
        c = Client(port)
        st, opts, _ = c.call("POST", "/api/passkey/login/options")
        good = auth.get(opts, f"https://{HOST}")
        assert c.call("POST", "/api/passkey/login/verify", good, origin="https://evil.com")[0] in (401, 403)  # cross-origin
        st, opts, _ = c.call("POST", "/api/passkey/login/options")
        replay = auth.get(opts, f"https://{HOST}")
        assert c.call("POST", "/api/passkey/login/verify", replay)[0] == 200
        assert c.call("POST", "/api/passkey/login/verify", replay)[0] == 401  # single use
        other = Client(port)
        assert login(other, Authenticator())[0] == 401  # unknown credential
        assert login(other, auth, rp_id="evil.trycloudflare.com")[0] == 401  # wrong rp id hash
        assert other.cookie is None
        no_uv = Authenticator(uv=False)
        assert register(owner, no_uv)[0] == 400  # user verification is required
        assert Client(port).call("GET", "/api/history?kind=actions", headers={"Cookie": "ctrl_auth=p.bogus"})[0] == 401
        assert Client(port).call("GET", "/", headers={"Cookie": "ctrl_auth=p.bogus"})[0] == 401
    finally:
        p.stop_remote()


def test_failures_feed_the_lockout(tmp_path):
    p, port, tok = setup(tmp_path)
    try:
        c = Client(port)
        c.call("POST", "/api/passkey/login/options")
        p._fails = []
        import time as t
        t0 = t.monotonic()
        for _ in range(10):
            p._fails.append(t0)
        assert c.call("POST", "/api/passkey/login/options")[0] == 429
    finally:
        p.stop_remote()


def test_token_login_can_be_turned_off(tmp_path):
    p, port, tok = setup(tmp_path)
    try:
        owner, auth = Client(port), Authenticator()
        owner.call("GET", "/?t=" + tok)
        assert owner.call("POST", "/api/passkey/token-login", {"enabled": False})[0] == 409  # no passkey yet: would lock out
        register(owner, auth)
        assert owner.call("POST", "/api/passkey/token-login", {"enabled": False})[0] == 200
        assert Client(port).call("GET", "/api/history?kind=actions", headers={"X-Ctrl-Token": tok})[0] == 401
        assert Client(port).call("GET", "/?t=" + tok)[0] == 401
        assert owner.call("GET", "/api/history?kind=actions")[0] == 401  # the old token cookie is dead too
        c = Client(port)
        assert login(c, auth)[0] == 200 and c.call("GET", "/api/history?kind=actions")[0] == 200
        # a new quick-tunnel hostname has no passkey yet, so the token still works there (never locked out)
        new = Client(port, host="zzz-new.trycloudflare.com")
        assert new.call("GET", "/api/history?kind=actions", headers={"X-Ctrl-Token": tok})[0] == 200
        assert new.call("GET", "/api/passkey/status", headers={"X-Ctrl-Token": tok})[1]["count"] == 0  # bound to the old host
        assert c.call("POST", "/api/passkey/token-login", {"enabled": True})[0] == 200
        assert Client(port).call("GET", "/api/history?kind=actions", headers={"X-Ctrl-Token": tok})[0] == 200
    finally:
        p.stop_remote()


def test_passkey_session_keeps_csrf_and_strict_cookie(tmp_path):
    p, port, tok = setup(tmp_path)
    try:
        owner, auth = Client(port), Authenticator()
        owner.call("GET", "/?t=" + tok)
        register(owner, auth)
        c = Client(port)
        st, opts, r = c.call("POST", "/api/passkey/login/options")
        st, _, r = c.call("POST", "/api/passkey/login/verify", auth.get(opts, f"https://{HOST}"))
        sc = r.getheader("Set-Cookie")
        assert "HttpOnly" in sc and "Secure" in sc and "SameSite=Strict" in sc
        assert c.call("POST", "/api/rerun", {"kind": "x", "id": "y"}, origin="https://evil.com")[0] == 403
        assert c.call("POST", "/api/rerun", {"kind": "actions", "id": "nope"})[0] == 200
        assert c.call("POST", "/api/passkey/register/options", origin="https://evil.com")[0] == 403
    finally:
        p.stop_remote()


def test_named_tunnel_host_is_accepted(tmp_path):
    p, port, tok = setup(tmp_path)
    try:
        assert Client(port, host="ctrl.example.com").call("GET", "/api/history?kind=actions", headers={"X-Ctrl-Token": tok})[0] == 421
        (tmp_path / "ctrl_tunnel_named.json").write_text(json.dumps({"name": "vibi", "hostname": "ctrl.example.com"}))
        assert Client(port, host="ctrl.example.com").call("GET", "/api/history?kind=actions", headers={"X-Ctrl-Token": tok})[0] == 200
    finally:
        p.stop_remote()
