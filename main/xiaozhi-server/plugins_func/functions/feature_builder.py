"""Voice-started background feature builds for the voice box and server repos.

Tools: build_feature, build_status.
Safety model: a build only starts after the spoken details-then-yes confirmation (mac_control's pending
step); it runs the Claude CLI in a separate git worktree on a new `voice/...` branch under DATA_DIR/builds, so the
live checkout is never touched; Claude may edit and commit there but git push, deletion, sudo and shells are denied;
one build runs at a time with a hard time limit. Merging stays a manual step. Progress and follow-ups go through
build_status (status / result / cancel) and build_feature(followup=true) continues the latest branch.
"""
import json
import os
import re
import signal
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from plugins_func.register import register_function, ToolType, ActionResponse, Action
from plugins_func.functions import mac_control as mc
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()

BUILDS_DIR = mc.DATA_DIR / "builds"
JOBS = BUILDS_DIR / "jobs.json"
BUILD_PROJECTS = tuple(mc.PROJECT_ENUM)  # every project mac_control knows: trading, voicebox, server
BUILD_TIMEOUT = int(os.environ.get("FEATURE_BUILD_TIMEOUT", str(30 * 60)))
VIBI = os.environ.get("VIBI_BIN", str(Path.home() / "esp/voice-note-box/bin/vibi"))
MAX_REQUEST_CHARS = 1500
BUILD_STYLE = (
    "You are building a feature on a dedicated git branch in a separate worktree. Make the change, add or update "
    "tests, commit your work on this branch with a clear message, and never run git push or touch any remote or "
    "other branch. Finish with a plain-speech summary of at most two short sentences saying what you built and "
    "whether tests pass; no code, paths or markdown in that summary."
)
DENY = mc.DENY_WRITE + ["Bash(git push:*)", "Bash(git checkout main*)", "Bash(git merge:*)"]

_lock = threading.Lock()
_procs = {}  # job id -> Popen


def _load():
    try:
        return json.loads(JOBS.read_text())
    except (OSError, ValueError):
        return []


def _save(jobs):
    BUILDS_DIR.mkdir(parents=True, exist_ok=True)
    JOBS.write_text(json.dumps(jobs, indent=1))


def _update(job_id, **fields):
    with _lock:
        jobs = _load()
        for j in jobs:
            if j["id"] == job_id:
                j.update(fields)
        _save(jobs)


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:30] or "feature"


def _notify(text):
    """Best effort: queue a spoken message through the vibi outbox if its CLI exists."""
    if os.path.exists(VIBI):
        try:
            mc._run([VIBI, "say", text], timeout=15)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass


def _latest():
    jobs = _load()
    return jobs[-1] if jobs else None


def _watch(job_id, proc):
    try:
        out, err = proc.communicate(timeout=BUILD_TIMEOUT)
        status = "done" if proc.returncode == 0 and (out or "").strip() else "failed"
        summary = mc._two_sentences(out) if status == "done" else mc._shorten((err or "no output").strip(), 150)
    except subprocess.TimeoutExpired:
        _kill(proc)
        status, summary = "timed_out", "It ran out of time."
    except Exception as e:  # never let the watcher thread die silently
        status, summary = "failed", str(e)
    with _lock:
        _procs.pop(job_id, None)
    job = next((j for j in _load() if j["id"] == job_id), {})
    if job.get("status") == "cancelled":
        return
    _update(job_id, status=status, summary=summary, finished=time.time())
    _notify(f"The build on branch {job.get('branch', '')} {'finished' if status == 'done' else status.replace('_', ' ')}. {summary}")


def _kill(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        proc.kill()


def _running():
    return next((j for j in _load() if j["status"] == "running" and j["id"] in _procs), None)


def _start(args):
    if _running():
        return ActionResponse(Action.RESPONSE, "build already running", "A build is already running. Ask for its status first.")
    project, request = args["project"], args["request"]
    repo = mc.PROJECTS.get(project)
    if not repo or not os.path.isdir(repo):
        return ActionResponse(Action.RESPONSE, "unknown project", "I can't find that project.")
    prev = _latest() if args.get("followup") else None
    if args.get("followup") and not (prev and prev["project"] == project and os.path.isdir(prev["path"])):
        return ActionResponse(Action.RESPONSE, "no build to continue", f"There's no earlier {project} build to continue.")
    job_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    if prev:
        branch, path = prev["branch"], prev["path"]
    else:
        branch = f"voice/{job_id}-{_slug(request)}"
        path = str(BUILDS_DIR / f"{project}_{job_id}")
        BUILDS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            r = mc._run(["git", "-C", repo, "worktree", "add", "-b", branch, path, "HEAD"], timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            return ActionResponse(Action.RESPONSE, f"worktree failed: {e}", "I couldn't set up a workspace for the build.")
        if r.returncode != 0:
            logger.bind(tag=TAG).error(f"worktree add failed: {r.stderr.strip()[:200]}")
            return ActionResponse(Action.RESPONSE, "worktree failed", "I couldn't set up a workspace for the build.")
    cmd = [mc.CLAUDE_BIN, "-p", request, "--append-system-prompt", BUILD_STYLE,
           "--allowedTools", *mc.WRITE_TOOLS, "--disallowedTools", *DENY, "--permission-mode", "acceptEdits"]
    try:
        proc = subprocess.Popen(cmd, cwd=path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                shell=False, start_new_session=True)
    except (OSError, ValueError) as e:
        return ActionResponse(Action.RESPONSE, f"claude failed: {e}", "I couldn't start Claude.")
    with _lock:
        _procs[job_id] = proc
        _save(_load() + [{"id": job_id, "project": project, "request": request, "branch": branch, "path": path,
                          "status": "running", "started": time.time(), "followup": bool(prev)}])
    threading.Thread(target=_watch, args=(job_id, proc), daemon=True).start()
    return ActionResponse(Action.RESPONSE, f"build {job_id} started on {branch}",
                          f"Started. I'll tell you when it's done. Ask for the build status any time.")


mc.register_pending_handler("build_start", _start)

build_feature_desc = {
    "type": "function",
    "function": {
        "name": "build_feature",
        "description": (
            "Have Claude build a new feature or change in a project: trading (the trading-signal desk), voicebox (the board software) or server (the XiaoZhi voice server), in the background on a separate git branch (never the live code, never pushed). Use when "
            "the user says 'build ...', 'add a feature ...' or 'ask Claude to build ...'. Set followup true to "
            "continue the most recent build's branch with further changes. It first reads the details back and "
            "asks for a spoken yes; only then call confirm_action."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "request": {"type": "string", "description": "What to build, in the user's words, with enough detail to act on."},
                "project": {"type": "string", "enum": list(BUILD_PROJECTS), "description": "trading (the trading-signal desk), voicebox (the board software) or server (the XiaoZhi voice server)."},
                "followup": {"type": "boolean", "description": "True to continue the previous build's branch."},
            },
            "required": ["request", "project"],
        },
    },
}


@register_function("build_feature", build_feature_desc, ToolType.SYSTEM_CTL)
def build_feature(conn, request, project, followup=False):
    request = (request or "").strip()
    if not request:
        return ActionResponse(Action.RESPONSE, "empty request", "What should I build?")
    if len(request) > MAX_REQUEST_CHARS:
        return ActionResponse(Action.RESPONSE, "request too long", "That request is too long. Please shorten it.")
    if project not in BUILD_PROJECTS:
        return ActionResponse(Action.RESPONSE, "bad project", "I can build in the trading, voice box or server project. Which one?")
    where = "continue the last build's branch" if followup else "start a new branch in a separate workspace"
    return mc._create_pending(
        "build_start", {"request": request, "project": project, "followup": bool(followup)},
        f"I'm about to have Claude build this in the {project} project and {where}, with no pushing and no changes to the live code: "
        f"{mc._shorten(request, 160)}.")


build_status_desc = {
    "type": "function",
    "function": {
        "name": "build_status",
        "description": (
            "Follow up on the latest background build: 'status' (is it still running?), 'result' (what it built "
            "and which branch to review) or 'cancel' (stop it). Use for 'how is the build going?', 'is it done?', "
            "'what did it build?', 'stop the build'."
        ),
        "parameters": {
            "type": "object",
            "properties": {"action": {"type": "string", "enum": ["status", "result", "cancel"]}},
            "required": [],
        },
    },
}


@register_function("build_status", build_status_desc, ToolType.SYSTEM_CTL)
def build_status(conn, action="status"):
    job = _latest()
    if not job:
        return ActionResponse(Action.RESPONSE, "no builds", "There are no builds yet.")
    if job["status"] == "running" and job["id"] not in _procs:  # server restarted while it ran
        _update(job["id"], status="lost")
        job["status"] = "lost"
    if action == "cancel":
        proc = _procs.get(job["id"])
        if job["status"] != "running" or proc is None:
            return ActionResponse(Action.RESPONSE, "nothing running", "No build is running.")
        _update(job["id"], status="cancelled", finished=time.time())
        _kill(proc)
        return ActionResponse(Action.RESPONSE, "cancelled", "Build stopped. Its branch is kept as it was.")
    mins = int((time.time() - job["started"]) // 60)
    if job["status"] == "running":
        return ActionResponse(Action.RESPONSE, "running", f"The {job['project']} build has been running for {mins} minutes.")
    if job["status"] == "done":
        what = f"{job['summary']} It's on branch {job['branch']}, ready for you to review." if action == "result" else "The build finished. Ask for the result."
        return ActionResponse(Action.RESPONSE, f"done {job['branch']}", what)
    if job["status"] == "lost":
        return ActionResponse(Action.RESPONSE, "lost", f"The server restarted during the build. Check branch {job['branch']} for partial work.")
    return ActionResponse(Action.RESPONSE, job["status"], f"The last build {job['status'].replace('_', ' ')}. {job.get('summary', '')}")
