import asyncio
import json
import time

import pytest
from websockets.asyncio.server import serve

from mac_app.audio import OpusDecoder, OpusEncoder
from mac_app.session import Session

FRAME30 = bytes(960)  # 30 ms of silence at 16 kHz
SERVER_HELLO = {"type": "hello", "session_id": "sess1", "audio_params": {"sample_rate": 24000, "frame_duration": 60}}


class FakeSpeaker:
    def __init__(self):
        self.played = []

    def play(self, pcm):
        self.played.append(pcm)

    def flush(self):
        pass

    def is_busy(self):
        return False


class Harness:
    """A fake xiaozhi server; `script(ws, rec)` drives it after the handshake."""

    def __init__(self, script):
        self.script = script
        self.headers = None
        self.texts = []
        self.binaries = []

    async def handler(self, ws):
        self.headers = dict(ws.request.headers)
        self.texts.append(json.loads(await ws.recv()))
        await ws.send(json.dumps(SERVER_HELLO))

        async def collect():
            try:
                async for m in ws:
                    (self.binaries if isinstance(m, bytes) else self.texts).append(m if isinstance(m, bytes) else json.loads(m))
            except Exception:
                pass

        task = asyncio.create_task(collect())
        try:
            await self.script(ws, self)
        finally:
            task.cancel()


async def run_session(script, idle_timeout=5.0, tail=0.3, feed=True, auto=False):
    h = Harness(script)
    states = []
    sp = FakeSpeaker()
    q = asyncio.Queue()
    async with serve(h.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        s = Session(f"ws://127.0.0.1:{port}/xiaozhi/v1/", q, sp, states.append, idle_timeout=idle_timeout, tail=tail)

        async def feeder():
            while True:
                await q.put(FRAME30)
                await asyncio.sleep(0.005)

        ft = asyncio.create_task(feeder()) if feed else None
        try:
            await asyncio.wait_for(s.run("hi bibi", auto=auto), 10)
        finally:
            if ft:
                ft.cancel()
    return h, states, sp


def tts(state):
    return json.dumps({"type": "tts", "state": state})


async def test_handshake_sends_hello_listen_detect():
    async def script(ws, h):
        await asyncio.sleep(0.2)
        await ws.close()

    h, states, _ = await run_session(script, feed=False)
    assert h.headers["device-id"] == "mac-app" and h.headers["client-id"] == "mac-app"
    assert h.texts[0]["type"] == "hello"
    assert h.texts[1]["state"] == "start" and h.texts[1]["session_id"] == "sess1"
    assert h.texts[2]["state"] == "detect" and h.texts[2]["text"] == "hi bibi"
    assert states[0] == "connecting" and states[-1] == "idle"


async def test_server_audio_is_played_and_states_reported():
    enc = OpusEncoder(24000)

    async def script(ws, h):
        await ws.send(tts("start"))
        await ws.send(enc.encode(bytes(2880)))
        await asyncio.sleep(0.05)
        await ws.send(tts("stop"))
        await asyncio.sleep(0.1)
        await ws.close()

    h, states, sp = await run_session(script, feed=False)
    assert len(sp.played) == 1 and len(sp.played[0]) == 2880
    assert "speaking" in states and "listening" in states


async def test_no_mic_while_speaking_nor_in_tail_then_resumes():
    seen = {}

    async def script(ws, h):
        await ws.send(tts("start"))
        await asyncio.sleep(0.3)
        seen["during"] = len(h.binaries)
        await ws.send(tts("stop"))
        await asyncio.sleep(0.15)
        seen["tail"] = len(h.binaries)
        await asyncio.sleep(0.6)
        seen["after"] = len(h.binaries)
        await ws.close()

    h, _, _ = await run_session(script)
    assert seen["during"] == 0 and seen["tail"] == 0 and seen["after"] > 0
    dec = OpusDecoder(16000, 60)
    assert len(dec.decode(h.binaries[0])) == 1920


async def test_idle_timeout_closes_and_returns_idle():
    async def script(ws, h):
        await ws.send(tts("start"))
        await ws.send(tts("stop"))
        await asyncio.wait_for(ws.wait_closed(), 3)  # the client must hang up on its own

    t0 = time.monotonic()
    _, states, _ = await run_session(script, idle_timeout=0.4, tail=0.05)
    assert time.monotonic() - t0 < 2.5
    assert states[-1] == "idle"


async def test_server_close_midsession_returns_cleanly():
    async def script(ws, h):
        await ws.close()

    _, states, _ = await run_session(script, feed=False)
    assert states[-1] == "idle"


async def test_connection_refused_reports_idle_without_raising():
    states = []
    s = Session("ws://127.0.0.1:1/xiaozhi/v1/", asyncio.Queue(), FakeSpeaker(), states.append)
    await s.run()
    assert states == ["connecting", "idle"]


async def test_auto_session_ends_after_speaking_when_nothing_was_asked(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_BOX_DATA", str(tmp_path))

    async def script(ws, h):
        await ws.send(tts("start"))
        await ws.send(tts("stop"))
        await asyncio.sleep(1.0)  # a normal session would now be listening; this one must have closed

    h, states, _ = await run_session(script, feed=False, auto=True)
    assert "listening" not in states and states[-1] == "idle"


async def test_auto_session_keeps_listening_when_it_asked_a_question(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_BOX_DATA", str(tmp_path))
    (tmp_path / "awaiting_reply_until").write_text(str(time.time() + 30))

    async def script(ws, h):
        await ws.send(tts("start"))
        await ws.send(tts("stop"))
        await asyncio.sleep(0.2)
        await ws.close()

    h, states, _ = await run_session(script, feed=False, auto=True)
    assert "listening" in states
