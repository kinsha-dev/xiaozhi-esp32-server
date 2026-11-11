#!/usr/bin/python3
"""Copies WhatsApp Desktop notifications from the macOS notification database into a small JSON inbox.

This is the only process that needs Full Disk Access; the XiaoZhi server then reads just the inbox file
(whatsapp_reader.py). Stdlib only, read-only on the database, writes one file (mode 600) under the data dir,
keeps the last 24 h / 50 messages, never touches the network. Run it as /usr/bin/python3 (see MAC_CONTROL.md).

  whatsapp_collector.py            # loop forever, refresh every 20 s
  whatsapp_collector.py --once     # one refresh (used by tests / launchd StartInterval)

It also collects important mail, by default from Gmail (read-only API, metadata + snippet only; see below), or from
Mac Mail when data/mail_digest.json has "source": "mac_mail". Important means (subject keyword / named sender from data/mail_digest.json), written into the same
all-apps inbox as app "mail", at most every 5 minutes. Gmail: scope gmail.readonly, refresh token in
data/google/token.json (made by bin/gmail_auth in voice-note-box); never fetches bodies, never modifies mail. Mac Mail
fallback: read-only AppleScript, only while Mail is already running.
Rows hold one-line gists only (never bodies); one-time-code / password-reset mails become a bare "code" row.
"""
import json
import os
import hashlib
import plistlib
import re
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(os.environ.get("MAC_CONTROL_DATA_DIR", Path.home() / "esp/xiaozhi-local/data"))
INBOX = DATA_DIR / "whatsapp_inbox.json"
ALL_INBOX = DATA_DIR / "notifications_inbox.json"  # every other app, for mac_notifications.py
NOTIF_DB = Path(os.environ.get(
    "WHATSAPP_NOTIF_DB", Path.home() / "Library/Group Containers/group.com.apple.usernoted/db2/db"))
APP_MATCH = "whatsapp"
APPLE_EPOCH = 978307200  # CFAbsoluteTime 0 = 2001-01-01 UTC
MAX_AGE = 24 * 3600
MAX_MESSAGES = 50
MAIL_CONFIG = Path(os.environ.get("MAIL_DIGEST_CONFIG", Path.home() / "esp/voice-note-box/data/mail_digest.json"))
GMAIL_TOKEN = Path(os.environ.get("GMAIL_TOKEN", Path.home() / "esp/voice-note-box/data/google/token.json"))
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
GMAIL_NOT_LINKED = "Gmail not linked yet"
GMAIL_MAX = 50
MAIL_EVERY = 300
DEFAULT_MAIL_KEYWORDS = ["urgent", "invoice", "payment", "interview", "offer", "security", "verify", "deadline",
                         "action required", "meeting"]
FS, RS = "\x1f", "\x1e"
CODE_RE = re.compile(
    r"(verification|security|one[- ]?time|login|log-in|sign[- ]?in|confirmation|access|auth\w*)\s+(code|pin)|\botp\b|"
    r"\bpasscode\b|\b(your|the)\s+code\s+is\b|reset\s+(your\s+)?password|password\s+reset|forgot\s+(your\s+)?password|"
    r"\b\d{4,8}\s+is\s+your\b", re.I)
URL_RE = re.compile(r"https?://\S+|www\.\S+")
# Read-only: subject, sender, date and the first 400 chars of content. Never opens, moves or marks mail; does not
# launch Mail (the caller checks it is running).
MAIL_SCRIPT = '''
set fs to (ASCII character 31)
set rs to (ASCII character 30)
set cutoff to (current date) - (24 * hours)
set out to ""
tell application "Mail"
    set found to (messages of inbox whose date received > cutoff)
    repeat with m in found
        set body to ""
        try
            set body to content of m
            if (length of body) > 400 then set body to text 1 thru 400 of body
        end try
        set d to date received of m
        set stamp to (year of d as string) & "-" & ((month of d) as integer) & "-" & (day of d) & "-" & (time of d)
        set out to out & (sender of m) & fs & (subject of m) & fs & stamp & fs & body & rs
    end repeat
end tell
return out
'''


def fetch(db=None):
    """[{id, sender, group, body, ts}] for WhatsApp notifications, newest first. Raises OSError."""
    try:
        con = sqlite3.connect(f"file:{db or NOTIF_DB}?mode=ro", uri=True, timeout=2)
        try:
            rows = con.execute(
                "SELECT r.rec_id, r.data, r.delivered_date FROM record r JOIN app a ON a.app_id = r.app_id "
                "WHERE lower(a.identifier) LIKE ? ORDER BY r.delivered_date DESC LIMIT 200",
                (f"%{APP_MATCH}%",),
            ).fetchall()
        finally:
            con.close()
    except sqlite3.DatabaseError as e:
        raise OSError(str(e))
    out = []
    for rec_id, blob, delivered in rows:
        try:
            req = plistlib.loads(blob).get("req", {})
        except Exception:
            continue
        body = str(req.get("body") or "").strip()
        if body:
            out.append({"id": str(rec_id), "sender": str(req.get("titl") or "Someone").strip(),
                        "group": str(req.get("subt") or "").strip(), "body": body,
                        "ts": float(delivered) + APPLE_EPOCH if delivered else time.time()})
    return out


def fetch_all(db=None):
    """[{id, app, title, sub, body, ts}] for non-WhatsApp notifications, newest first. Raises OSError."""
    try:
        con = sqlite3.connect(f"file:{db or NOTIF_DB}?mode=ro", uri=True, timeout=2)
        try:
            rows = con.execute(
                "SELECT r.rec_id, a.identifier, r.data, r.delivered_date FROM record r JOIN app a ON a.app_id = r.app_id "
                "WHERE lower(a.identifier) NOT LIKE ? ORDER BY r.delivered_date DESC LIMIT 200",
                (f"%{APP_MATCH}%",),
            ).fetchall()
        finally:
            con.close()
    except sqlite3.DatabaseError as e:
        raise OSError(str(e))
    out = []
    for rec_id, ident, blob, delivered in rows:
        try:
            req = plistlib.loads(blob).get("req", {})
        except Exception:
            continue
        body = str(req.get("body") or "").strip()
        title = str(req.get("titl") or "").strip()
        if "scripteditor" in str(ident).lower():  # vibi's own "Voice box" banners; they are spoken already
            continue
        if body or title:
            out.append({"id": f"n{rec_id}", "app": str(ident).rsplit(".", 1)[-1], "title": title,
                        "sub": str(req.get("subt") or "").strip(), "body": body,
                        "ts": float(delivered) + APPLE_EPOCH if delivered else time.time()})
    return out


def mail_config():
    cfg = {"keywords": DEFAULT_MAIL_KEYWORDS, "senders": []}
    try:
        data = json.loads(MAIL_CONFIG.read_text())
        cfg["source"] = str(data.get("source", "gmail")).strip().lower()
        for k in ("keywords", "senders"):
            if isinstance(data.get(k), list):
                cfg[k] = data[k]
    except (OSError, ValueError):
        pass
    out = {k: [str(x).strip().lower() for x in cfg[k] if str(x).strip()] for k in ("keywords", "senders")}
    out["source"] = cfg.get("source", "gmail")
    return out


def mail_important(sender, subject, cfg):
    return any(x in sender.lower() for x in cfg["senders"]) or any(k in subject.lower() for k in cfg["keywords"])


def mail_sender_name(sender):
    m = re.match(r'\s*"?([^"<]+?)"?\s*<', sender)
    return ((m.group(1) if m else sender.split("@")[0]).strip() or sender.strip())


def mail_gist(preview, limit=80):
    t = " ".join(URL_RE.sub("", preview or "").split())
    t = re.split(r"(?<=[.!?;:])\s|\s[-\u2013\u2014|]\s", t, maxsplit=1)[0].strip(" .-")
    return t if len(t) <= limit else t[: limit - 1].rsplit(" ", 1)[0] + "\u2026"


def mail_rows(mails, cfg=None, now=None):
    """Inbox rows [{id, app:"mail", title, sub, body, ts[, code]}] for the important mails. Gists only."""
    cfg = cfg or mail_config()
    out = []
    for m in mails:
        if not mail_important(m["sender"], m["subject"], cfg):
            continue
        rid = "m" + hashlib.sha1(f"{m['sender']}|{m['subject']}|{m.get('date', '')}".encode()).hexdigest()[:12]
        ts = m.get("ts")
        if ts is None:  # unparseable date: skip rather than pin the mail inside the 24 h window forever
            continue
        if CODE_RE.search(f"{m['subject']}\n{m.get('preview', '')}"):
            out.append({"id": rid, "app": "mail", "title": "code or password-reset mail (not shown)", "sub": "",
                        "body": "", "ts": ts, "code": True})
        else:
            out.append({"id": rid, "app": "mail", "title": mail_sender_name(m["sender"]),
                        "sub": " ".join(m["subject"].split())[:80], "body": mail_gist(m.get("preview", "")), "ts": ts})
    return out


def parse_stamp(stamp):
    """'Y-M-D-secondsSinceMidnight' (local time) -> epoch seconds, or None."""
    try:
        y, mo, d, secs = (int(x) for x in stamp.split("-"))
        return datetime(y, mo, d).timestamp() + secs
    except (ValueError, OverflowError):
        return None


class GmailError(Exception):
    pass


_gmail_token = {"access": "", "exp": 0.0}


def _gmail_access_token(now=None):
    """Access token from the stored refresh token (stdlib only). Raises GmailError(GMAIL_NOT_LINKED) when unlinked."""
    now = now if now is not None else time.time()
    if _gmail_token["access"] and now < _gmail_token["exp"] - 60:
        return _gmail_token["access"]
    try:
        tok = json.loads(GMAIL_TOKEN.read_text())
        body = urllib.parse.urlencode({"client_id": tok["client_id"], "client_secret": tok["client_secret"],
                                       "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"}).encode()
        url = tok.get("token_uri") or "https://oauth2.googleapis.com/token"
    except (OSError, ValueError, KeyError, TypeError):
        raise GmailError(GMAIL_NOT_LINKED)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=15) as r:
            data = json.load(r)
        _gmail_token.update(access=data["access_token"], exp=now + float(data.get("expires_in", 3600)))
    except Exception as e:  # never log the response: it can hold tokens
        raise GmailError(f"Gmail sign-in failed ({type(e).__name__}); run bin/gmail_auth again")
    return _gmail_token["access"]


def _gmail_get(path, params, access):
    req = urllib.request.Request(f"{GMAIL_API}/{path}?{urllib.parse.urlencode(params, doseq=True)}",
                                 headers={"Authorization": f"Bearer {access}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def fetch_gmail(getter=None, access=None):
    """Raw mails from Gmail's inbox, last 24 h: users.messages.list + get(format=metadata: Subject/From + snippet).
    Read-only; no bodies. Raises GmailError."""
    access = access or _gmail_access_token()
    get = getter or (lambda path, params: _gmail_get(path, params, access))
    try:
        ids = [m["id"] for m in get("messages", {"q": "in:inbox newer_than:1d", "maxResults": GMAIL_MAX}).get("messages", [])]
        mails = []
        for mid in ids:
            m = get(f"messages/{mid}", {"format": "metadata", "metadataHeaders": ["Subject", "From"]})
            h = {x["name"].lower(): x["value"] for x in m.get("payload", {}).get("headers", [])}
            mails.append({"sender": h.get("from", ""), "subject": h.get("subject", ""), "date": mid,
                          "preview": m.get("snippet", ""), "ts": float(m["internalDate"]) / 1000})
        return mails
    except GmailError:
        raise
    except Exception as e:
        raise GmailError(f"Gmail read failed ({type(e).__name__})")


mail_status = {"error": ""}


def fetch_mail():
    """Raw mails from the configured source (Gmail by default, Mac Mail fallback); [] with mail_status set on failure."""
    mail_status["error"] = ""
    if mail_config()["source"] != "mac_mail":
        try:
            return fetch_gmail()
        except GmailError as e:
            mail_status["error"] = str(e)
            print(f"whatsapp_collector: mail: {e}", file=sys.stderr)
            return []
    return fetch_mac_mail()


def fetch_mac_mail():
    """Raw mails from Mac Mail's inbox (last 24 h), [] when Mail is not running or blocked. Never raises."""
    try:
        up = subprocess.run(["osascript", "-e", 'application "Mail" is running'], capture_output=True, text=True, timeout=10)
        if up.stdout.strip() != "true":
            return []
        r = subprocess.run(["osascript", "-e", MAIL_SCRIPT], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"whatsapp_collector: mail: {e}", file=sys.stderr)
        return []
    if r.returncode != 0:
        print(f"whatsapp_collector: mail: {r.stderr.strip()[:200]}", file=sys.stderr)
        return []
    mails = []
    for rec in r.stdout.split(RS):
        p = rec.strip("\n").split(FS)
        if len(p) >= 4:
            mails.append({"sender": p[0], "subject": p[1], "date": p[2], "preview": p[3], "ts": parse_stamp(p[2])})
    return mails


_mail_cache = {"at": 0.0, "rows": []}


def mail_rows_cached(fetcher=None, now=None):
    now = now if now is not None else time.time()
    if now - _mail_cache["at"] >= MAIL_EVERY:
        _mail_cache["rows"] = mail_rows((fetcher or fetch_mail)(), now=now)
        _mail_cache["at"] = now
    return _mail_cache["rows"]


def _write(path, msgs, **extra):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"updated": time.time(), "messages": msgs, **extra}))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def refresh_once():
    cutoff = time.time() - MAX_AGE
    msgs = [m for m in fetch() if m["ts"] >= cutoff][:MAX_MESSAGES]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _write(INBOX, msgs)
    allm = [m for m in fetch_all() + mail_rows_cached() if m["ts"] >= cutoff]
    allm.sort(key=lambda m: m["ts"], reverse=True)
    _write(ALL_INBOX, allm[:MAX_MESSAGES], mail_error=mail_status["error"])
    return len(msgs)


def main(argv):
    once = "--once" in argv
    misses = 0
    while True:
        try:
            refresh_once()
            misses = 0
        except OSError as e:
            print(f"whatsapp_collector: {e}", file=sys.stderr)
            if once:
                return 1
            misses += 1
            if misses >= 3:  # access to the database is sometimes lost until the process restarts: let launchd restart it
                return 1
        if once:
            return 0
        time.sleep(20)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
