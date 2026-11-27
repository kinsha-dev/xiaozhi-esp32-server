"""A typed command never ends in an empty reply: Chat shows progress, then the answer or the real reason; and paste."""
import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from mac_app import ctrl_backend as be
from mac_app.config import Config
from mac_app.controller import Controller
from mac_app.session import Session
from mac_app.tests.test_mac_app_session import FakeSpeaker, run_session
from mac_app.tests.test_mute import FakeDetector, FakeMic

HTML = (Path(__file__).parents[1] / "ctrl_ui" / "index.html").read_text()
BOARD = """import json
def messages(data_dir, source=None):
    return []
def post_message(text, data_dir, **kw):
    open(str(data_dir) + '/posted.json', 'w').write(json.dumps({'text': text, **kw}))
"""


@pytest.fixture
def data(tmp_path):
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "message_board.py").write_text(BOARD)
    d = tmp_path / "data"
    d.mkdir()
    return d


def _session(url, **kw):
    return Session(url, asyncio.Queue(), FakeSpeaker(), lambda s: None, **kw)


def test_slow_server_is_reported_not_swallowed():
    async def go():
        async def mute(reader, writer):  # accepts the connection, never answers the handshake
            await asyncio.sleep(5)

        server = await asyncio.start_server(mute, "127.0.0.1", 0)
        s = _session(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/", connect_timeout=0.3)
        await asyncio.wait_for(s.run("open safari"), 5)
        server.close()
        return s

    s = asyncio.run(go())
    assert not s.sent and "did not answer within 0.3 s" in s.error and "Nothing ran" in s.error


def test_unreachable_server_is_reported():
    s = _session("ws://127.0.0.1:1/")
    asyncio.run(s.run("open safari"))
    assert "could not reach the voice server" in s.error


def test_the_default_wait_covers_a_busy_mac():
    assert _session("ws://x").connect_timeout >= 30


def test_server_closing_without_a_reply_is_reported():
    async def script(ws, rec):
        await asyncio.sleep(0.2)
        await ws.close()

    h, _, _ = asyncio.run(run_session(script, quiet=True, feed=False))
    assert h.session.sent and "closed" in h.session.error


def _run_typed(data, factory, stealth):
    if stealth:
        (data / "stealth").write_text("1")
    ctl = Controller(Config("ws://x", "hi bibi", data, 24000), FakeSpeaker(), FakeDetector(), lambda cb: FakeMic(cb),
                     factory, lambda t: None)
    ctl.start()
    assert ctl.start_session("open insta", force=True)
    for _ in range(200):
        if not ctl._active:
            break
        time.sleep(0.02)
    ctl.stop()
    return json.loads((data / "posted.json").read_text()) if (data / "posted.json").exists() else None


def _fake(reply=(), error="", seen=None):
    class S:
        def __init__(self, q, on_state):
            self.reply, self.error = list(reply), error

        async def run(self, text, auto=False):
            if seen is not None:
                seen.append(be.pending_typed(self.data) if hasattr(self, "data") else None)

    return S


@pytest.mark.parametrize("stealth", [True, False])
def test_failed_typed_command_shows_the_reason(data, stealth):
    row = _run_typed(data, _fake(error="Nothing ran: the voice server did not answer within 30 s."), stealth)
    assert row["status"] == "failed" and "did not answer" in row["text"] and "(no reply)" not in row["text"]
    assert be.pending_typed(data) is None


def test_stealth_reply_is_never_empty(data):
    row = _run_typed(data, _fake(), True)
    assert row["status"] == "failed" and "Vibi: Vibi sent no text reply." in row["text"]


def test_spoken_success_is_not_filed_twice(data):
    assert _run_typed(data, _fake(reply=["Done."]), False) is None


def test_progress_while_waiting_and_while_computer_use_runs(data):
    be.set_pending(data, "open insta")
    rows = be.messages(data, hours=0)
    assert [(r["status"], r["body"]) for r in rows] == [("running", "Waiting for Vibi to answer “open insta”…")]
    (data / "history.jsonl").write_text(json.dumps({"id": "r", "ts": time.time() + 1, "kind": "reply", "text": "On it."}) + "\n")
    cu = data / "computer_use"
    cu.mkdir()
    state = {"deadline": time.time() + 200, "steps": 3, "max_steps": 30, "started": time.time()}
    (cu / "state.json").write_text(json.dumps(state))
    bodies = {r["body"]: r for r in be.messages(data, hours=0)}
    assert not any(b.startswith("Waiting") for b in bodies)  # answered
    row = bodies["Computer use is working… step 3 of 30."]
    assert row["type"] == "computer" and row["status"] == "running"
    (cu / "state.json").write_text(json.dumps({**state, "deadline": time.time() - 100}))
    assert any("stopped without a result" in r["body"] and r["status"] == "failed" for r in be.messages(data, hours=0))


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_which_pastes_become_attachments():
    line = next(x for x in HTML.splitlines() if x.startswith("function pastedFile("))
    cases = [(["Files"], ["shot"], "shot"), (["Files", "text/plain"], ["finder"], "finder"), (["text/plain"], [], None),
             (["text/plain", "text/html", "Files"], ["render"], None), (["text/html", "Files"], ["webimage"], "webimage")]
    script = line + f"\nconsole.log(JSON.stringify({json.dumps(cases)}.map(c=>pastedFile({{types:c[0],files:c[1]}}))))"
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    assert out == [c[2] for c in cases]
    assert 'name:"paste"' in HTML and "navigator.clipboard.read()" in HTML
