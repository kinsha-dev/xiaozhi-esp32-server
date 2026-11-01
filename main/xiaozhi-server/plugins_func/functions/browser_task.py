"""Voice-started browser tasks through jev-ultrafast (browser-use/jev-ultrafast, MIT, pinned clone).

Tool: browser_task(url, goal). The agent opens its own background tab in the Mac's Chrome (Browser Harness over CDP),
reads the page's controls and clicks/types toward one narrow goal, then stops. Page text goes to api.typesafe.ai and
the OpenRouter endpoint in the clone's .env; nothing else leaves the machine and harness telemetry is switched off.
Safety model: https public addresses only; goals about logging in, paying, buying, sending, posting, deleting,
booking or submitting forms are refused; no spoken confirmation (the owner-voice gate on the speaker is the check); 2 minute hard limit;
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
        status = next((l for l in reversed(lines) if re.search(r"\d+ actions", l)), "")
        did = next((l[4:].strip() for l in lines if l.startswith("did:")), "")
        final = urlparse(lines[-1])
        where = (final.hostname or "the page") + (mc._shorten(final.path, 40) if final.path not in ("", "/") else "")
        acted = int((re.search(r"(\d+) actions", status) or [0, 0])[1])
        # The agent saying DONE is not proof: with no click or typing it only looked at the page.
        done = ("done" in status.lower() or "complete" in status.lower()) and acted > 0
        if not done:
            return ActionResponse(Action.RESPONSE, f"{status}\n{did}\n{lines[-1]}",
                                  f"I couldn't finish that. I ended on {where}.")
        return ActionResponse(Action.RESPONSE, f"{status}\n{did}\n{lines[-1]}",
                              f"Done. {mc._shorten(did, 120)}. I ended on {where}." if did else f"Done. I ended on {where}.")
    finally:
        _busy.release()


browser_task_desc = {
    "type": "function",
    "function": {
        "name": "browser_task",
        "description": (
            "Use the browser: have the browser agent click and type on a public website in the user's Chrome, shown "
            "on screen. Use ONLY when the user says 'use the browser', 'ask the browser', 'go to <site> and ...' or "
            "'open <site> and find/click ...', e.g. 'use the browser to open the Wikipedia article about Godel'. "
            "For questions the user wants answered aloud ('search the web for', 'look up', 'what is') use "
            "search_web; for GitHub use github_search. It cannot log in, pay, buy, send, post or submit forms. It "
            "runs straight away; do not call confirm_action."
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
    return _run_jev({"url": clean, "goal": goal})
