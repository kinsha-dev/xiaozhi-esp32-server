#!/usr/bin/env python3
"""Claude Code Stop hook: post the end-of-turn follow-up to the voice box.

Reads the hook JSON on stdin, takes the last assistant message from the transcript, reduces it to plain speech
(at most two short sentences, no code, paths or markdown) and queues it with `vibi say`. Only fires for sessions
whose working directory is under VIBI_HOOK_DIRS (colon separated; default: the three voice-box projects and
the voice-started builds), and never when Claude is already continuing from a stop hook. Always exits 0 so it
can never block or break a session. Stdlib only; message text goes to the local outbox and nowhere else.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
VIBI = os.environ.get("VIBI_BIN", str(HOME / "esp/voice-note-box/bin/vibi"))
DEFAULT_DIRS = [HOME / "Spx_Trading_Signal", HOME / "esp/voice-note-box", HOME / "esp/xiaozhi-server",
                HOME / "esp/xiaozhi-local/data/builds"]
MAX_CHARS = 220


def hook_dirs():
    raw = os.environ.get("VIBI_HOOK_DIRS")
    return [Path(p) for p in raw.split(":") if p] if raw else DEFAULT_DIRS


def in_scope(cwd):
    try:
        cwd = Path(cwd).resolve()
    except (OSError, ValueError, TypeError):
        return False
    return any(cwd == d or d in cwd.parents for d in (x.resolve() for x in hook_dirs()))


def last_assistant_text(transcript):
    """Text of the last assistant message in a Claude Code JSONL transcript ('' if none)."""
    text = ""
    try:
        lines = Path(transcript).read_text().splitlines()
    except (OSError, ValueError, TypeError):
        return ""
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        msg = row.get("message") if isinstance(row, dict) else None
        if row.get("type") == "assistant" and isinstance(msg, dict):
            parts = msg.get("content")
            if isinstance(parts, str):
                got = parts
            else:
                got = " ".join(p.get("text", "") for p in parts or [] if isinstance(p, dict) and p.get("type") == "text")
            if got.strip():
                text = got
    return text


def to_speech(text):
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\b[\w./~-]*/[\w./-]+\b|\b[\w-]+\.(?:js|py|json|jsonl|md|html|sh|yaml|yml|ts|txt)\b", "a file", text)
    text = re.sub(r"[*#>|_]+", " ", text)
    text = " ".join(text.split())
    out = " ".join(re.split(r"(?<=[.!?])\s+", text)[:2])
    return out if len(out) <= MAX_CHARS else out[: MAX_CHARS - 3].rsplit(" ", 1)[0] + "..."


def main():
    try:
        data = json.load(sys.stdin)
        if data.get("stop_hook_active") or not in_scope(data.get("cwd", "")):
            return 0
        speech = to_speech(last_assistant_text(data.get("transcript_path")))
        if speech and os.path.exists(VIBI):
            subprocess.run([VIBI, "say", f"Claude: {speech}"], timeout=15, capture_output=True, shell=False)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
