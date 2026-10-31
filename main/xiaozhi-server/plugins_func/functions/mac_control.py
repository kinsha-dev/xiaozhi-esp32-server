"""Voice control of the desk's pi agent and the Mac for the local XiaoZhi server.

Tools: ask_pi, mac_control, confirm_action.
Safety model: fixed argument-list subprocess calls only (never a shell), an allowlist file for
apps/shortcuts, pi (read + bash, no deleting) by default, and a spoken one-time token for anything risky.
The token is only given in the spoken reply (not in the text returned to the LLM), so the
model cannot confirm on its own; the user has to say it back.
"""
import json
import os
import re
import secrets
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from plugins_func.register import register_function, ToolType, ActionResponse, Action
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()

DATA_DIR = Path(os.environ.get("MAC_CONTROL_DATA_DIR", Path.home() / "esp/xiaozhi-local/data"))
ALLOWLIST = DATA_DIR / "control_allowlist.json"
TASKS_DIR = DATA_DIR / "pi_tasks"
# pi (the desk's sandboxed coding agent) replaces the Claude CLI. server/pi_ask.js wraps it: read+bash
# tools, project-folder writes only, and the OS-level sandbox denies deleting project files.
PI_CWD = os.environ.get("MAC_CONTROL_PI_CWD", "/Users/kinsha/Spx_Trading_Signal")
PI_NODE = os.environ.get("MAC_CONTROL_NODE_BIN", "/opt/homebrew/opt/node@22/bin/node")
PI_WRAPPER = os.environ.get("MAC_CONTROL_PI_WRAPPER", str(Path.home() / "esp/voice-note-box/server/pi_ask.js"))
PI_TIMEOUT = 145  # just under the server's 150 s tool-call limit; pi_ask.js stops itself at 140 s
PI_DAILY_CAP = int(os.environ.get("MAC_CONTROL_PI_DAILY_CAP", "20"))
PI_RUNS = DATA_DIR / "pi_runs.json"
# Projects the agents may work in. "trading" is the default; the others only when the user names them.
PROJECTS = {
    "trading": PI_CWD,
    "voicebox": str(Path.home() / "esp/voice-note-box"),
    "server": str(Path.home() / "esp/xiaozhi-server"),
}
PROJECT_ENUM = list(PROJECTS)
CLAUDE_CWD = os.environ.get("MAC_CONTROL_CLAUDE_CWD", "/Users/kinsha/Spx_Trading_Signal")  # default project
CLAUDE_BIN = os.environ.get("MAC_CONTROL_CLAUDE_BIN", "claude")
CLAUDE_TIMEOUT = 120
# Bash is allowed (so Claude can run e.g. `node pipeline_status.js` for live status) but deletion is
# denied. Deny rules beat allow rules in Claude Code. This is a guard against the obvious, not a
# sandbox: scripting languages (node -e, python -c) or `mv` over a file can still destroy data.
BASH_DENY = [
    "Bash(rm:*)", "Bash(rmdir:*)", "Bash(unlink:*)", "Bash(shred:*)", "Bash(trash:*)",
    "Bash(find * -delete*)", "Bash(find * -exec rm*)", "Bash(git clean:*)", "Bash(git reset --hard*)",
    "Bash(sudo:*)", "Bash(xargs:*)", "Bash(eval:*)", "Bash(sh:*)", "Bash(bash:*)", "Bash(zsh:*)",
]
READ_TOOLS = ["Read", "Grep", "Glob", "Bash"]
# Confirmed "change" mode adds file edits on top.
WRITE_TOOLS = READ_TOOLS + ["Edit", "Write"]
_NO_WEB = ["NotebookEdit", "WebFetch", "WebSearch"]
DENY_READ = ["Edit", "Write"] + _NO_WEB + BASH_DENY
DENY_WRITE = _NO_WEB + BASH_DENY
CLAUDE_TASKS_DIR = DATA_DIR / "claude_tasks"
CONFIRM_TTL = 60
MAX_TASK_CHARS = 1000
DEFAULT_ALLOWLIST = {"apps": ["Safari", "Notes", "Calendar", "Music", "Spotify", "Terminal"], "shortcuts": []}


# Words suggesting the task wants to change files or run something.
_RISKY = re.compile(
    r"\b(edit|change|modify|fix|write|create|add|update|rename|refactor|implement|delete|remove|"
    r"install|commit|push|deploy|restart|kill|move|overwrite|apply|patch|save)\b",
    re.I,
)

_lock = threading.Lock()
_pending = {}  # token -> {"kind", "args", "expires"}
_handlers = {}  # kind -> fn(args) -> ActionResponse; lets other plugins reuse the spoken confirmation


def register_pending_handler(kind, fn):
    _handlers[kind] = fn


def _now():
    return time.time()


def _load_allowlist():
    """Read the allowlist, creating a sensible example file when missing."""
    if not ALLOWLIST.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        ALLOWLIST.write_text(json.dumps(DEFAULT_ALLOWLIST, indent=2) + "\n")
        return dict(DEFAULT_ALLOWLIST)
    try:
        data = json.loads(ALLOWLIST.read_text())
    except (OSError, ValueError):
        return {"apps": [], "shortcuts": []}
    return {
        "apps": [str(a) for a in data.get("apps", [])],
        "shortcuts": [str(s) for s in data.get("shortcuts", [])],
    }


def _match(name, allowed):
    """Case-insensitive exact match against the allowlist; returns the canonical name."""
    name = (name or "").strip().lower()
    for item in allowed:
        if item.lower() == name:
            return item
    return None


def _create_pending(kind, args, prompt):
    now = _now()
    with _lock:
        for t in [t for t, p in _pending.items() if p["expires"] < now]:
            del _pending[t]
        token = f"{secrets.randbelow(9000) + 1000}"
        while token in _pending:
            token = f"{secrets.randbelow(9000) + 1000}"
        _pending[token] = {"kind": kind, "args": args, "expires": now + CONFIRM_TTL}
    digits = " ".join(token)
    spoken = f"{prompt} To go ahead, say confirm {digits}. That expires in a minute."
    return ActionResponse(Action.RESPONSE, f"Confirmation required for: {prompt} (token sent by voice only)", spoken)


def _project_dir(project, default):
    """Folder for a named project; unknown or missing names fall back to the default (trading) project."""
    path = default if project in (None, "", "trading") else PROJECTS.get(project, default)
    return path if os.path.isdir(path) else default


def _no_push(project):
    """Pushes are blocked for the voice-box repos (the trading repo keeps its own, confirm-gated, behaviour)."""
    return project in ("voicebox", "server")


def _run(cmd, timeout=15, cwd=None, env=None):
    """Single choke point for subprocesses: list args only, never a shell."""
    assert isinstance(cmd, list) and all(isinstance(c, str) for c in cmd)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env, shell=False)


def _shorten(text, limit=220):
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rsplit(" ", 1)[0] + "..."


def _plain_speech(text):
    """Strip markdown/code marks so the voice never reads backticks, asterisks or file paths aloud."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\b[\w./-]+\.(?:js|py|json|jsonl|md|html|sh|yaml|yml|ts|txt)\b", "a file", text)
    text = re.sub(r"[*#>|]+", " ", text)
    return text


def _two_sentences(text):
    text = " ".join(_plain_speech(text).split())
    parts = re.split(r"(?<=[.!?])\s+", text)
    return _shorten(" ".join(parts[:2]))


# ---------------------------------------------------------------------------------------------
# pi (the desk's coding agent)
# ---------------------------------------------------------------------------------------------
# The first two sentences of the answer are read aloud, so they must work as speech.
SPOKEN_STYLE = (
    "Your answer is read aloud by a voice assistant. Begin with a plain-speech summary of at most two short "
    "sentences that states the result directly: no code, backticks, markdown, file names, paths or commands, "
    "and do not start by explaining your tools or limits. Put the detail after that summary. "
    "If you need live data you may run read-only shell commands; you cannot delete files."
)


def _pi_runs_today():
    try:
        d = json.loads(PI_RUNS.read_text())
    except (OSError, ValueError):
        d = {}
    return d if d.get("day") == datetime.now().strftime("%Y-%m-%d") else {"day": datetime.now().strftime("%Y-%m-%d"), "count": 0}


def _count_pi_run():
    d = _pi_runs_today()
    d["count"] += 1
    PI_RUNS.parent.mkdir(parents=True, exist_ok=True)
    PI_RUNS.write_text(json.dumps(d))


def _run_pi(task, write=False, project=None):
    if _pi_runs_today()["count"] >= PI_DAILY_CAP:
        return ActionResponse(Action.RESPONSE, "daily cap", f"I've already run pi {PI_DAILY_CAP} times today, so I'll stop for now.")
    style = SPOKEN_STYLE + (" Never run git push or touch any remote." if _no_push(project) else "")
    cmd = [PI_NODE, PI_WRAPPER, "edit" if write else "read", f"{style}\n\nTask: {task}"]
    _count_pi_run()
    try:
        # pi's launcher is `#!/usr/bin/env node`; the server's launchd PATH has no node (keg-only node@22).
        workdir = _project_dir(project, PI_CWD)
        env = {**os.environ, "PATH": f"{Path(PI_NODE).parent}:{os.environ.get('PATH', '')}", "PI_ASK_DIR": workdir}
        r = _run(cmd, timeout=PI_TIMEOUT, cwd=workdir, env=env)
    except subprocess.TimeoutExpired:
        return ActionResponse(Action.RESPONSE, "pi timed out", "Pi took too long, so I stopped it.")
    except (OSError, ValueError) as e:
        logger.bind(tag=TAG).error(f"pi failed to start: {e}")
        return ActionResponse(Action.RESPONSE, f"pi failed: {e}", "I couldn't start pi.")
    answer = _final_answer(r.stdout)
    if r.returncode in (124, 143):  # pi_ask.js stops itself at 140 s (SIGTERM)
        return ActionResponse(Action.RESPONSE, "pi timed out", "Pi ran out of time on that, so I stopped it. Try a smaller request.")
    if r.returncode != 0 or not answer:
        err = _shorten((r.stderr or "no output").strip(), 200)
        logger.bind(tag=TAG).error(f"pi exit {r.returncode}: {err}")
        return ActionResponse(Action.RESPONSE, f"pi exit {r.returncode}: {err}", "Pi didn't give an answer.")
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    path = TASKS_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    path.write_text(f"# Task\n\n{task}\n\n# Mode\n\n{'edit' if write else 'read + bash (no deleting)'}\n\n# Answer\n\n{answer}\n")
    spoken = f"{_two_sentences(answer)} The full answer is saved in pi tasks, file {path.stem}."
    return ActionResponse(Action.RESPONSE, f"Saved to {path}\n{answer[:500]}", spoken)


def _final_answer(stdout):
    """pi prints dotenv's banner line first; drop it and keep the answer."""
    lines = [l for l in (stdout or "").splitlines() if not l.startswith("\u25c7 injected env")]
    return "\n".join(lines).strip()


ask_pi_desc = {
    "type": "function",
    "function": {
        "name": "ask_pi",
        "description": (
            "Ask pi, the desk's coding agent on the user's Mac, to look at the trading-signal project, run "
            "commands to check live status, and answer. It can read files and run shell commands but cannot "
            "delete anything. Use when the user says 'ask pi ...' or 'have the agent check ...' or asks for the desk "
            "pipeline status without naming Claude. If they say 'ask Claude' (or 'cloud', 'claud', 'clod'), use ask_claude instead. Set allow_changes true only if the user explicitly wants files edited; "
            "that asks the user for spoken confirmation first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The question or task, in plain words."},
                "project": {"type": "string", "enum": PROJECT_ENUM, "description": "Which project: trading (default, the trading-signal desk), voicebox (the voice box / ESP32 board software, the 'vibi' project) or server (the XiaoZhi voice server). Say voicebox or server only when the user names the voice box, the ESP, the board or the voice server."},
                "allow_changes": {"type": "boolean", "description": "True only if the task requires editing files."},
            },
            "required": ["task"],
        },
    },
}


@register_function("ask_pi", ask_pi_desc, ToolType.SYSTEM_CTL)
def ask_pi(conn, task, allow_changes=False, project="trading"):
    task = (task or "").strip()
    if not task:
        return ActionResponse(Action.RESPONSE, "empty task", "What should I ask pi?")
    if len(task) > MAX_TASK_CHARS:
        return ActionResponse(Action.RESPONSE, "task too long", "That request is too long. Please shorten it.")
    if allow_changes or _RISKY.search(task):
        return _create_pending("pi_write", {"task": task, "project": project}, "That task may edit files in your project.")
    return _run_pi(task, write=False, project=project)


# ---------------------------------------------------------------------------------------------
# Claude Code (kept alongside pi: the user says 'ask pi' or 'ask Claude')
# ---------------------------------------------------------------------------------------------
def _run_claude(task, write=False, project=None):
    cmd = [
        CLAUDE_BIN, "-p", task,
        "--append-system-prompt", SPOKEN_STYLE,
        "--allowedTools", *(WRITE_TOOLS if write else READ_TOOLS),
        "--disallowedTools", *(DENY_WRITE if write else DENY_READ), *(["Bash(git push:*)"] if _no_push(project) else []),
        "--permission-mode", "acceptEdits" if write else "default",
    ]
    try:
        r = _run(cmd, timeout=CLAUDE_TIMEOUT, cwd=_project_dir(project, CLAUDE_CWD))
    except subprocess.TimeoutExpired:
        return ActionResponse(Action.RESPONSE, "claude timed out", "Claude took longer than two minutes, so I stopped it.")
    except (OSError, ValueError) as e:
        logger.bind(tag=TAG).error(f"claude failed to start: {e}")
        return ActionResponse(Action.RESPONSE, f"claude failed: {e}", "I couldn't start Claude.")
    answer = (r.stdout or "").strip()
    if r.returncode != 0 or not answer:
        err = _shorten((r.stderr or "no output").strip(), 200)
        return ActionResponse(Action.RESPONSE, f"claude exit {r.returncode}: {err}", "Claude didn't give an answer.")
    CLAUDE_TASKS_DIR.mkdir(parents=True, exist_ok=True)
    path = CLAUDE_TASKS_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    path.write_text(f"# Task\n\n{task}\n\n# Mode\n\n{'edit' if write else 'read-only'}\n\n# Answer\n\n{answer}\n")
    spoken = f"{_two_sentences(answer)} The full answer is saved in claude tasks, file {path.stem}."
    return ActionResponse(Action.RESPONSE, f"Saved to {path}\n{answer[:500]}", spoken)


ask_claude_desc = {
    "type": "function",
    "function": {
        "name": "ask_claude",
        "description": (
            "Ask Claude Code (Anthropic's Claude CLI) on the user's Mac to look at their trading-signal project and answer. Use whenever the user says 'ask Claude' (the speech recogniser often hears the name as 'cloud', 'claud' or 'clod': treat those as Claude); when they say 'ask pi' use ask_pi. "
            "It can read files and run shell commands but deletion is denied. "
            "Set allow_changes true only if the user explicitly wants files edited; "
            "that will ask the user for spoken confirmation first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The question or task for Claude, in plain words."},
                "project": {"type": "string", "enum": PROJECT_ENUM, "description": "Which project: trading (default, the trading-signal desk), voicebox (the voice box / ESP32 board software, the 'vibi' project) or server (the XiaoZhi voice server). Say voicebox or server only when the user names the voice box, the ESP, the board or the voice server."},
                "allow_changes": {"type": "boolean", "description": "True only if the task requires editing files."},
            },
            "required": ["task"],
        },
    },
}


@register_function("ask_claude", ask_claude_desc, ToolType.SYSTEM_CTL)
def ask_claude(conn, task, allow_changes=False, project="trading"):
    task = (task or "").strip()
    if not task:
        return ActionResponse(Action.RESPONSE, "empty task", "What should I ask Claude?")
    if len(task) > MAX_TASK_CHARS:
        return ActionResponse(Action.RESPONSE, "task too long", "That request is too long. Please shorten it.")
    if allow_changes or _RISKY.search(task):
        return _create_pending("claude_write", {"task": task, "project": project}, "That task may edit files in your project.")
    return _run_claude(task, write=False, project=project)


# ---------------------------------------------------------------------------------------------
# Mac actions
# ---------------------------------------------------------------------------------------------
ACTIONS = ("set_volume", "mute", "play_pause", "lock_screen", "open_app", "run_shortcut")
CONFIRM_ACTIONS = ("lock_screen", "run_shortcut")


def _validate(action, arg):
    """Return (clean_arg, error). Raises nothing."""
    if action not in ACTIONS:
        return None, "Unknown action."
    if action == "set_volume":
        try:
            level = int(float(str(arg).strip().rstrip("%")))
        except (TypeError, ValueError):
            return None, "Volume must be a number from 0 to 100."
        if not 0 <= level <= 100:
            return None, "Volume must be between 0 and 100."
        return level, None
    if action == "open_app":
        app = _match(arg, _load_allowlist()["apps"])
        return (app, None) if app else (None, "That app isn't in your allowed list.")
    if action == "run_shortcut":
        sc = _match(arg, _load_allowlist()["shortcuts"])
        return (sc, None) if sc else (None, "That shortcut isn't in your allowed list.")
    return None, None


def _execute(action, arg):
    if action == "set_volume":
        r = _run(["osascript", "-e", f"set volume output volume {int(arg)}"])
        spoken = f"Volume set to {int(arg)}."
    elif action == "mute":
        r = _run(["osascript", "-e", "set volume with output muted"])
        spoken = "Muted."
    elif action == "play_pause":
        # Media key via System Events (key code 100 is F8 = play/pause on Apple keyboards).
        r = _run(["osascript", "-e", 'tell application "System Events" to key code 100'])
        spoken = "Toggled play and pause."
    elif action == "lock_screen":
        r = _run(["pmset", "displaysleepnow"])
        spoken = "Locking the screen."
    elif action == "open_app":
        r = _run(["open", "-a", arg])
        spoken = f"Opening {arg}."
    elif action == "run_shortcut":
        r = _run(["shortcuts", "run", arg], timeout=60)
        spoken = f"Ran the {arg} shortcut."
    else:
        return ActionResponse(Action.RESPONSE, "unknown action", "I can't do that.")
    if r.returncode != 0:
        err = _shorten((r.stderr or "").strip(), 150)
        logger.bind(tag=TAG).warning(f"{action} failed: {err}")
        return ActionResponse(Action.RESPONSE, f"{action} failed: {err}", "That didn't work.")
    return ActionResponse(Action.RESPONSE, f"{action} ok", spoken)


mac_control_desc = {
    "type": "function",
    "function": {
        "name": "mac_control",
        "description": (
            "Control the user's Mac. Actions: set_volume (arg 0-100), mute, play_pause, lock_screen, "
            "open_app (arg app name), run_shortcut (arg shortcut name). Only allowlisted apps and shortcuts "
            "work. lock_screen and run_shortcut ask for spoken confirmation first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(ACTIONS), "description": "What to do."},
                "arg": {"type": "string", "description": "Volume level, app name or shortcut name, when the action needs one."},
            },
            "required": ["action"],
        },
    },
}


@register_function("mac_control", mac_control_desc, ToolType.SYSTEM_CTL)
def mac_control(conn, action, arg=None):
    action = (action or "").strip()
    clean, err = _validate(action, arg)
    if err:
        return ActionResponse(Action.RESPONSE, err, err)
    if action in CONFIRM_ACTIONS:
        what = "lock the screen" if action == "lock_screen" else f"run the {clean} shortcut"
        return _create_pending("mac", {"action": action, "arg": clean}, f"I'm about to {what}.")
    try:
        return _execute(action, clean)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.bind(tag=TAG).warning(f"{action} error: {e}")
        return ActionResponse(Action.RESPONSE, f"{action} error: {e}", "That didn't work.")


# ---------------------------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------------------------
confirm_action_desc = {
    "type": "function",
    "function": {
        "name": "confirm_action",
        "description": (
            "Carry out a pending risky action after the user spoke the confirmation digits aloud, e.g. "
            "'confirm 4 8 2 1'. Pass the digits exactly as the user said them, never guess or invent them."
        ),
        "parameters": {
            "type": "object",
            "properties": {"token": {"type": "string", "description": "The four digits the user said."}},
            "required": ["token"],
        },
    },
}


@register_function("confirm_action", confirm_action_desc, ToolType.SYSTEM_CTL)
def confirm_action(conn, token):
    token = re.sub(r"\D", "", str(token or ""))
    with _lock:
        item = _pending.pop(token, None)  # single-use: removed whether valid or expired
    if item is None or item["expires"] < _now():
        return ActionResponse(Action.RESPONSE, "invalid or expired token", "That code is wrong or has expired. Please ask again.")
    if item["kind"] == "claude_write":
        return _run_claude(item["args"]["task"], write=True, project=item["args"].get("project"))
    if item["kind"] == "pi_write":
        return _run_pi(item["args"]["task"], write=True, project=item["args"].get("project"))
    if item["kind"] == "mac":
        a = item["args"]
        # Re-validate: the allowlist may have changed since the request.
        clean, err = _validate(a["action"], a["arg"])
        if err:
            return ActionResponse(Action.RESPONSE, err, err)
        try:
            return _execute(a["action"], clean)
        except (OSError, subprocess.TimeoutExpired) as e:
            return ActionResponse(Action.RESPONSE, f"error: {e}", "That didn't work.")
    if item["kind"] in _handlers:
        return _handlers[item["kind"]](item["args"])
    return ActionResponse(Action.RESPONSE, "unknown pending", "Nothing to confirm.")
