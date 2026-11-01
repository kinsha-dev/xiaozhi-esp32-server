"""Voice-started browser tasks through jev-ultrafast (browser-use/jev-ultrafast, MIT, pinned clone).

Tool: browser_task(url, goal). The agent opens its own background tab in the Mac's Chrome (Browser Harness over CDP),
reads the page's controls and clicks/types toward one narrow goal, then stops. Page text goes to api.typesafe.ai and
the OpenRouter endpoint in the clone's .env; nothing else leaves the machine and harness telemetry is switched off.
Safety model: https public addresses only; goals about logging in, paying, buying, sending, posting, deleting,
booking or submitting forms are refused; a spoken yes naming the site and goal is needed first; 2 minute hard limit;
one task at a time; it shares Chrome's signed-in profile, so the refusal list is the main protection.
"""
import ipaddress
import os
import re
import signal
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlparse

from plugins_func.register import register_function, ToolType, ActionResponse, Action
from plugins_func.functions import mac_control as mc
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()

JEV_DIR = Path(os.environ.get("JEV_DIR", Path.home() / "esp/xiaozhi-local/vendor/jev-ultrafast"))
UV_BIN = os.environ.get("UV_BIN", "/opt/homebrew/bin/uv")
# Runs jev with an OpenRouter chat model as the action chooser (TypeSafe is not used).
RUNNER = str(Path(__file__).resolve().parents[2] / "scripts" / "jev_openrouter_run.py")
TIMEOUT = int(os.environ.get("BROWSER_TASK_TIMEOUT", "120"))
MAX_GOAL_CHARS = 300
BLOCKED_GOAL = re.compile(
    r"\b(log ?in|sign ?in|sign ?up|register|password|passcode|pay|payment|buy|purchase|checkout|check out|order|"
    r"credit|card|bank|transfer|send|post|publish|tweet|comment|reply|delete|remove|cancel|book|reserve|"
    r"subscribe|submit|apply|download|install)\b", re.I)

_busy = threading.Lock()


def _public_https(url):
    """Return (clean_url, error). https only, a real hostname, never localhost or a private address."""
    p = urlparse((url or "").strip())
    host = p.hostname or ""
    if p.scheme != "https" or not host:
        return None, "I only open https web addresses."
    try:
        ip = ipaddress.ip_address(host)
        return None, "I won't open an address by number."
    except ValueError:
        pass
    if "." not in host or host.endswith((".local", ".internal", ".lan")) or host == "localhost":
        return None, "That isn't a public website."
    return p.geturl(), None


def _run_jev(args):
    url, goal = args["url"], args["goal"]
    if not _busy.acquire(blocking=False):
        return ActionResponse(Action.RESPONSE, "busy", "A browser task is already running.")
    try:
        env = {**os.environ, "BH_TELEMETRY": "0", "BROWSER_HARNESS_TELEMETRY": "0", "ANONYMIZED_TELEMETRY": "0"}
        cmd = [UV_BIN, "run", "--env-file", ".env", "python", RUNNER, "--url", url, "--goal", goal]
        try:
            proc = subprocess.Popen(cmd, cwd=str(JEV_DIR), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, shell=False, start_new_session=True)
        except (OSError, ValueError) as e:
            logger.bind(tag=TAG).error(f"jev failed to start: {e}")
            return ActionResponse(Action.RESPONSE, f"jev failed: {e}", "I couldn't start the browser agent.")
        try:
            out, err = proc.communicate(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                proc.kill()
            proc.communicate()
            return ActionResponse(Action.RESPONSE, "browser task timed out", "The browser task ran out of time, so I stopped it.")
        lines = [l for l in (out or "").splitlines() if l.strip()]
        if proc.returncode != 0 or not lines:
            msg = mc._shorten((err or "no output").strip().splitlines()[-1] if (err or "").strip() else "no output", 150)
            logger.bind(tag=TAG).warning(f"jev exit {proc.returncode}: {msg}")
            return ActionResponse(Action.RESPONSE, f"jev exit {proc.returncode}: {msg}", "The browser task didn't work.")
        status = lines[-2] if len(lines) > 1 else ""
        final = urlparse(lines[-1])
        where = (final.hostname or "the page") + (mc._shorten(final.path, 40) if final.path not in ("", "/") else "")
        done = "done" in status.lower() or "complete" in status.lower()
        return ActionResponse(Action.RESPONSE, f"{status}\n{lines[-1]}",
                              f"{'Done' if done else 'I stopped'}. I ended on {where}.")
    finally:
        _busy.release()


mc.register_pending_handler("browser_task", _run_jev)

browser_task_desc = {
    "type": "function",
    "function": {
        "name": "browser_task",
        "description": (
            "Have the browser agent carry out one narrow task on a public website in the user's Chrome, e.g. 'open "
            "the Wikipedia article about Godel' or 'search github.com for esp32'. It cannot log in, pay, buy, send, "
            "post or submit forms. It reads the site name and goal back and asks for a spoken yes; only then call "
            "confirm_action. For just reading a page aloud use read_page instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full https address to start from, e.g. https://github.com."},
                "goal": {"type": "string", "description": "One narrow goal in plain words."},
            },
            "required": ["url", "goal"],
        },
    },
}


@register_function("browser_task", browser_task_desc, ToolType.SYSTEM_CTL)
def browser_task(conn, url, goal):
    goal = " ".join((goal or "").split())
    clean, err = _public_https(url)
    if err:
        return ActionResponse(Action.RESPONSE, err, err)
    if not goal or len(goal) > MAX_GOAL_CHARS:
        return ActionResponse(Action.RESPONSE, "bad goal", "Tell me one short goal for the browser.")
    if BLOCKED_GOAL.search(goal):
        return ActionResponse(Action.RESPONSE, "blocked goal", "I won't do that in the browser: no logins, payments, sending, posting or forms.")
    if not (JEV_DIR / ".env").exists():
        return ActionResponse(Action.RESPONSE, "jev not configured", "The browser agent isn't set up yet.")
    host = urlparse(clean).hostname
    return mc._create_pending("browser_task", {"url": clean, "goal": goal},
                              f"I'm about to let the browser agent work on {host} in your Chrome with this goal: {mc._shorten(goal, 150)}.")
