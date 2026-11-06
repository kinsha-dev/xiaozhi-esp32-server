#!/usr/bin/env python3
"""Claude Code SessionStart hook: have the session listen for voice messages left for its project.

Reads the hook JSON on stdin and, for every project (any cwd below the home directory; VIBI_HOOK_DIRS, if set,
narrows it to those folders), registers this session as the project's latest listener and adds
context telling Claude to run `vibi listen --project <project>` in the background and to start it again each time
it returns. The project is the git repo's folder name (the main checkout's, inside a worktree), which is what
`vibi` matches "tell Claude <project>, ..." against. VIBI_LISTEN_PROJECT overrides the name. Always exits 0. Stdlib only.
"""
import json
import os
import subprocess
import sys
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
        sess_arg = ""
        if session:
            register(project, session)
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
