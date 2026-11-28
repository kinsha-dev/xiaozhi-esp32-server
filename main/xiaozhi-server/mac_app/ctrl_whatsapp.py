"""Reply on WhatsApp from the Chat tab: the same way the voice tool sends (a wa.me draft in WhatsApp Desktop, then
Return), but only after the user confirmed who gets it, and Return is pressed only if WhatsApp is in front."""
import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

LIMIT = 4000
LOAD_DELAY_S = 2.5
MIN_DIGITS = 7
MARKS = re.compile(r"[‎‏‪-‮⁦-⁩]")  # direction marks WhatsApp puts around names
SEND = '''tell application "WhatsApp" to activate
delay 0.5
tell application "System Events"
  if name of first application process whose frontmost is true does not contain "WhatsApp" then error "not in front"
  key code 36
end tell'''


def _name(text) -> str:
    return " ".join(MARKS.sub("", str(text or "")).split())


def _saved(data_dir: Path) -> dict:
    """{casefolded name: (name, number)} from data/whatsapp_contacts.json."""
    try:
        saved = json.loads((Path(data_dir) / "whatsapp_contacts.json").read_text(encoding="utf-8"))
        return {_name(k).casefold(): (_name(k), str(v)) for k, v in saved.items()}
    except (OSError, ValueError, AttributeError):
        return {}


def senders(data_dir: Path, texts: list) -> list:
    """Who each received message is from. A phone notification is one line "Name. message" (or "Name: group sender.
    message"), so this takes the longest saved contact name the line starts with, else what precedes the first ". " or
    ": " for the user to correct."""
    saved, out = _saved(data_dir), []
    for text in texts:
        line = _name(str(text or "").split("\n")[0].split(" — ")[0])
        line = re.sub(r"(?i)^new notification from whatsapp: ", "", line)
        ends = [len(line)] + [m.start() for m in re.finditer(r"[.:,](?= |$)| - ", line)]
        known = [line[:e].strip() for e in sorted(ends, reverse=True) if line[:e].strip().casefold() in saved]
        out.append(known[0] if known else re.split(r"[.:](?= |$)", line)[0].strip()[:60])
    return out


def resolve(data_dir: Path, contact: str) -> dict:
    """{"contact", "phone"} for a saved contact's exact name (any case) or a phone number. ValueError otherwise."""
    name = _name(contact)
    if not name:
        raise ValueError("who should get it?")
    if re.fullmatch(r"\+?[\d ()-]+", name) and len(re.sub(r"\D", "", name)) >= MIN_DIGITS:
        return {"contact": name, "phone": re.sub(r"\D", "", name)}
    found = _saved(data_dir).get(name.casefold(), ("", None))[1]
    if not found or len(re.sub(r"\D", "", found)) < MIN_DIGITS:
        raise ValueError(f"no WhatsApp number saved for {name[:60]}: type the number with country code")
    return {"contact": name, "phone": re.sub(r"\D", "", found)}


def send(data_dir: Path, contact: str, text: str, phone: str, run=subprocess.run, wait=time.sleep) -> dict:
    """Send `text`. `phone` is the number the user confirmed; if the name now gives another one, nothing is sent."""
    text = str(text or "").strip()
    if not text or len(text) > LIMIT:
        raise ValueError("empty or too long")
    who = resolve(data_dir, contact)
    if who["phone"] != phone:
        raise ValueError("the number changed: try again")
    url = f"https://wa.me/{who['phone']}?text={quote(text)}"
    try:
        run(["open", "-a", "WhatsApp", url], check=True, timeout=10, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        raise ValueError("could not open WhatsApp")
    wait(LOAD_DELAY_S)
    try:
        run(["osascript", "-e", SEND], check=True, timeout=15, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        raise ValueError("not sent: the draft is open in WhatsApp, press Return there")
    row = {"ts": datetime.now().isoformat(timespec="seconds"), "contact": who["contact"], "phone": who["phone"], "message": text}
    with open(Path(data_dir) / "whatsapp_messages.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return {"sent": True, "contact": who["contact"]}
