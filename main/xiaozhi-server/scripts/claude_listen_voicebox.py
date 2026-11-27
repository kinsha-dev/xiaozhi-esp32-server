#!/usr/bin/env python3
"""Claude Code SessionStart hook: have the session listen for voice messages left for its project.

Reads the hook JSON on stdin and, for every project (any cwd below the home directory; VIBI_HOOK_DIRS, if set,
narrows it to those folders), registers this session as the project's latest listener and adds
context telling Claude to run `vibi listen --project <project>` in the background and to start it again each time
it returns. The project is the git repo's folder name (the main checkout's, inside a worktree), which is what
`vibi` matches "tell Claude <project>, ..." against. VIBI_LISTEN_PROJECT overrides the name. The first session of a project pins itself in
data/listeners/<project>.main; while it is alive (its listener is running, or it was active in the last
GRACE_SECONDS) other sessions neither register nor listen. After that the next new session takes over. Always exits 0. Stdlib only.
"""
import json
import os
import re
import subprocess
import sys
import time
from glob import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_stop_voicebox import VIBI, in_scope  # noqa: E402

DATA = Path(os.environ.get("VOICE_BOX_DATA", Path.home() / "esp/voice-note-box/data"))


def wanted(cwd):
    if os.environ.get("VIBI_HOOK_DIRS"):
        return in_scope(cwd)
    try:
        return Path(cwd).resolve() != Path.home() and Path.home() in Path(cwd).resolve().parents
    except (OSError, ValueError, TypeError):
        return False


GRACE_SECONDS = 30 * 60


def pin_file(project):
    return DATA / "listeners" / (project.replace("/", "_") + ".main")


def pinned(project):
    """Session id the project's listener is pinned to, or ""."""
    try:
        return pin_file(project).read_text().strip()
    except OSError:
        return ""


def listener_running(session):
    try:
        return subprocess.run(["pgrep", "-f", f"vibi listen .*--session {re.escape(session)}"],
                              capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return True  # cannot tell: leave the pin alone


def last_active(project, session):
    """Newest of: when the pin was written, when the session's transcript last changed."""
    claude = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
    times = []
    for f in [pin_file(project), *claude.glob(f"projects/*/{escape(session)}.jsonl")]:
        try:
            times.append(f.stat().st_mtime)
        except OSError:
            pass
    return max(times, default=0)


def alive(project, session):
    return listener_running(session) or time.time() - last_active(project, session) < GRACE_SECONDS


def register(project, session):
    """Latest session wins: older sessions of the project see this id change and stop listening."""
    d = DATA / "listeners"
    d.mkdir(parents=True, exist_ok=True)
    (d / project.replace("/", "_")).write_text(session)


# The voice box lives in two repos; both are just "voice box" to the user, so the repo folder names never appear as a project.
ALIASES = {"xiaozhi-server": "voice box", "voice-note-box": "voice box"}


def project_name(cwd):
    name = Path(cwd).name
    try:
        common = subprocess.run(["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True, timeout=5).stdout.strip()
        if common:
            name = Path(common).parent.name
    except (OSError, subprocess.SubprocessError):
        pass
    return ALIASES.get(name, name)


def main():
    try:
        data = json.load(sys.stdin)
        cwd = data.get("cwd", "")
        if not wanted(cwd) or not os.path.exists(VIBI):
            return 0
        project = os.environ.get("VIBI_LISTEN_PROJECT") or project_name(cwd)
        session = data.get("session_id") or ""
        main_session = pinned(project)
        if main_session and session != main_session and alive(project, main_session):
            return 0  # the pinned session still owns this project's listener
        sess_arg = ""
        if session:
            register(project, session)
            if session != main_session:
                pin_file(project).write_text(session)
            sess_arg = f" --session {session}"
        cmd = f'{VIBI} listen --project "{project}"{sess_arg}'
        note = (f"Voice box: listen for messages the user leaves for this project by voice. Now run `{cmd}` with Bash "
                "run_in_background true. When it completes, read its output (JSON lines, each a message text for you), "
                "act on it as the user's request, then start the same command again in the background. "
                "It has no time limit; if it ever exits with a timeout, just start it again; if it prints {\"superseded\": true} a newer session of this project took over, so do not restart it.")
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": note}}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
