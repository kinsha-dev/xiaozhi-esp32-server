import importlib.util
import io
import json
import os
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("claude_listen_voicebox", SCRIPTS / "claude_listen_voicebox.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


@pytest.fixture
def run(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hook, "DATA", tmp_path / "data")
    monkeypatch.setattr(hook, "VIBI", str(SCRIPTS / "claude_listen_voicebox.py"))
    monkeypatch.setattr(hook, "listener_running", lambda session: False)
    monkeypatch.setattr(hook, "wanted", lambda cwd: True)
    monkeypatch.setenv("VIBI_LISTEN_PROJECT", "demo")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))

    def start(session):
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": str(tmp_path), "session_id": session})))
        assert hook.main() == 0
        return "listen" in capsys.readouterr().out

    return start


def age_pin():
    old = time.time() - hook.GRACE_SECONDS - 60
    os.utime(hook.pin_file("demo"), (old, old))


def test_first_session_pins_and_others_are_kept_out(run):
    assert run("first")
    assert not run("second")
    assert hook.pinned("demo") == "first"
    assert run("first")


def test_dead_pinned_session_is_replaced(run):
    run("first")
    age_pin()
    assert run("second")
    assert hook.pinned("demo") == "second"


def test_running_listener_keeps_old_pin(run, monkeypatch):
    run("first")
    age_pin()
    monkeypatch.setattr(hook, "listener_running", lambda session: True)
    assert not run("second")


def test_recent_transcript_keeps_old_pin(run, tmp_path):
    run("first")
    age_pin()
    transcript = tmp_path / "claude" / "projects" / "p" / "first.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}")
    assert not run("second")
