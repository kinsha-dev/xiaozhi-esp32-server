"""Passkey (WebAuthn) login for Vibi Ctrl's remote listener.

A passkey is bound to the relying-party id = the tunnel hostname (taken from the Host header, which ctrl._host_ok has
already restricted to *.trycloudflare.com or the configured named-tunnel host). Credentials (id, public key, sign count)
live in data/ctrl_passkeys.json (0600). User verification is always required. Sessions are random ids kept in memory
(a restart of the Mac app logs remote browsers out); no secret is ever written to a log or a response body."""
import base64
import json
import os
import secrets
import threading
import time
from pathlib import Path

from webauthn import (generate_authentication_options, generate_registration_options, options_to_json,
                      verify_authentication_response, verify_registration_response)
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
                                      ResidentKeyRequirement, UserVerificationRequirement)

SESSION_TTL = 604800
CHALLENGE_TTL = 120
RP_NAME = "Vibi Ctrl"


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Passkeys:
    def __init__(self, data_dir):
        self.file = Path(data_dir) / "ctrl_passkeys.json"
        self._lock = threading.Lock()
        self._challenges = {}  # cid -> (kind, rp_id, challenge bytes, expiry)
        self._sessions = {}  # sid -> expiry

    # ---- storage -----------------------------------------------------------------------------------------------
    def _load(self):
        try:
            d = json.loads(self.file.read_text())
        except (OSError, ValueError):
            d = {}
        d.setdefault("credentials", [])
        d.setdefault("token_login", True)
        if not d.get("user_id"):
            d["user_id"] = b64u(secrets.token_bytes(16))
        return d

    def _save(self, d):
        tmp = self.file.with_name(self.file.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(d, fh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.file)

    def count(self, rp_id):
        return sum(1 for c in self._load()["credentials"] if c["rp_id"] == rp_id)

    def token_login_enabled(self, rp_id):
        """Token login stays available on a hostname that has no passkey yet, so a new quick-tunnel hostname can never
        lock the owner out; once a passkey exists for the host it can be switched off."""
        d = self._load()
        return d["token_login"] or not any(c["rp_id"] == rp_id for c in d["credentials"])

    def set_token_login(self, rp_id, enabled):
        with self._lock:
            d = self._load()
            if not enabled and not any(c["rp_id"] == rp_id for c in d["credentials"]):
                return False  # refuse: it would leave no way in
            d["token_login"] = bool(enabled)
            self._save(d)
            return True

    # ---- challenges ----------------------------------------------------------------------------------------------
    def _put(self, kind, rp_id, challenge):
        now = time.monotonic()
        self._challenges = {k: v for k, v in self._challenges.items() if v[3] > now}
        if len(self._challenges) > 50:
            self._challenges.clear()
        cid = secrets.token_urlsafe(16)
        self._challenges[cid] = (kind, rp_id, challenge, now + CHALLENGE_TTL)
        return cid

    def _take(self, kind, rp_id, cid):
        v = self._challenges.pop(str(cid), None)  # single use
        if not v or v[0] != kind or v[1] != rp_id or v[3] < time.monotonic():
            raise ValueError("challenge expired")
        return v[2]

    # ---- registration ----------------------------------------------------------------------------------------------
    def register_options(self, rp_id):
        d = self._load()
        opts = generate_registration_options(
            rp_id=rp_id, rp_name=RP_NAME, user_id=unb64u(d["user_id"]), user_name="vibi-owner", user_display_name="Vibi owner",
            authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.PREFERRED,
                                                                   user_verification=UserVerificationRequirement.REQUIRED),
            exclude_credentials=[PublicKeyCredentialDescriptor(id=unb64u(c["id"])) for c in d["credentials"] if c["rp_id"] == rp_id])
        return {"cid": self._put("reg", rp_id, opts.challenge), "options": json.loads(options_to_json(opts))}

    def register_verify(self, rp_id, origin, cid, credential, name=""):
        with self._lock:
            v = verify_registration_response(credential=credential, expected_challenge=self._take("reg", rp_id, cid),
                                             expected_rp_id=rp_id, expected_origin=origin, require_user_verification=True)
            d = self._load()
            d["credentials"].append({"id": b64u(v.credential_id), "public_key": b64u(v.credential_public_key), "sign_count": v.sign_count,
                                     "rp_id": rp_id, "name": (name or "passkey")[:40], "created": int(time.time())})
            self._save(d)
        return True

    # ---- login -------------------------------------------------------------------------------------------------------
    def login_options(self, rp_id):
        opts = generate_authentication_options(rp_id=rp_id, user_verification=UserVerificationRequirement.REQUIRED)  # discoverable
        return {"cid": self._put("auth", rp_id, opts.challenge), "options": json.loads(options_to_json(opts))}

    def login_verify(self, rp_id, origin, cid, credential):
        """Returns a new session id, or raises."""
        with self._lock:
            challenge = self._take("auth", rp_id, cid)
            d = self._load()
            raw = credential.get("rawId") or credential.get("id") if isinstance(credential, dict) else None
            cred = next((c for c in d["credentials"] if c["rp_id"] == rp_id and c["id"] == raw), None)
            if not cred:
                raise ValueError("unknown credential")
            v = verify_authentication_response(credential=credential, expected_challenge=challenge, expected_rp_id=rp_id,
                                               expected_origin=origin, credential_public_key=unb64u(cred["public_key"]),
                                               credential_current_sign_count=cred["sign_count"], require_user_verification=True)
            cred["sign_count"] = v.new_sign_count
            self._save(d)
        return self.new_session()

    # ---- sessions -----------------------------------------------------------------------------------------------------
    def new_session(self):
        now = time.monotonic()
        self._sessions = {k: e for k, e in self._sessions.items() if e > now}
        sid = secrets.token_urlsafe(32)
        self._sessions[sid] = now + SESSION_TTL
        return sid

    def session_ok(self, sid):
        e = self._sessions.get(sid or "")
        return bool(e and e > time.monotonic())


LOGIN_HTML = b"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vibi Ctrl</title><style>:root{color-scheme:light dark}body{font:16px system-ui;display:grid;place-items:center;min-height:100vh;margin:0}
main{max-width:22rem;padding:1.5rem;text-align:center}button{font:inherit;padding:.7rem 1.2rem;border-radius:.6rem;border:1px solid;cursor:pointer}
#m{min-height:1.5em;margin-top:1rem;opacity:.8}</style></head><body><main><h1>Vibi Ctrl</h1>
<button id="b">Sign in with passkey</button><p id="m"></p></main><script src="/passkey.js"></script></body></html>"""

JS = r"""(function(){
const E=b=>btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
const D=s=>{s=s.replace(/-/g,'+').replace(/_/g,'/');while(s.length%4)s+='=';return Uint8Array.from(atob(s),c=>c.charCodeAt(0)).buffer};
const post=(p,b)=>fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})}).then(async r=>{const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.error||r.status);return j});
const say=t=>{const m=document.getElementById('m')||document.getElementById('pk-msg');if(m)m.textContent=t};
async function login(){
 try{say('Waiting for your passkey…');const {cid,options:o}=await post('/api/passkey/login/options');
  o.challenge=D(o.challenge);(o.allowCredentials||[]).forEach(c=>c.id=D(c.id));
  const c=await navigator.credentials.get({publicKey:o});const r=c.response;
  await post('/api/passkey/login/verify',{cid,credential:{id:c.id,rawId:E(c.rawId),type:c.type,clientExtensionResults:{},response:{
   authenticatorData:E(r.authenticatorData),clientDataJSON:E(r.clientDataJSON),signature:E(r.signature),userHandle:r.userHandle?E(r.userHandle):null}}});
  location.replace('/');}catch(e){say('Sign-in failed: '+e.message)}}
async function register(){
 try{say('Waiting for your device…');const {cid,options:o}=await post('/api/passkey/register/options');
  o.challenge=D(o.challenge);o.user.id=D(o.user.id);(o.excludeCredentials||[]).forEach(c=>c.id=D(c.id));
  const c=await navigator.credentials.create({publicKey:o});const r=c.response;
  await post('/api/passkey/register/verify',{cid,name:navigator.platform||'passkey',credential:{id:c.id,rawId:E(c.rawId),type:c.type,clientExtensionResults:{},response:{
   attestationObject:E(r.attestationObject),clientDataJSON:E(r.clientDataJSON),transports:r.getTransports?r.getTransports():[]}}});
  say('Passkey registered.');panel();}catch(e){say('Registration failed: '+e.message)}}
async function panel(){
 const s=await fetch('/api/passkey/status').then(r=>r.json());let box=document.getElementById('pk-box');
 if(!box){box=document.createElement('div');box.id='pk-box';box.style.cssText='position:fixed;right:12px;bottom:12px;z-index:99;padding:10px 12px;border-radius:10px;font:13px system-ui;background:#222;color:#eee;box-shadow:0 2px 10px #0006;max-width:260px';document.body.appendChild(box)}
 box.innerHTML='<b>Passkeys</b>: '+s.count+' on this host<br><button id="pk-add">Add passkey</button> <button id="pk-tok" '+(s.count?'':'disabled')+'>Token login: '+(s.token_login?'on':'off')+'</button><div id="pk-msg"></div>';
 document.getElementById('pk-add').onclick=register;
 document.getElementById('pk-tok').onclick=async()=>{try{await post('/api/passkey/token-login',{enabled:!s.token_login});panel()}catch(e){say(e.message)}};}
const b=document.getElementById('b');
if(b){b.onclick=login;if(window.PublicKeyCredential)setTimeout(login,300);else say('This browser has no passkey support.')}else panel().catch(()=>{});
})();"""
