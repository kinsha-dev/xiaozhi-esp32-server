"""One conversation with the xiaozhi server, speaking the same websocket protocol as the ESP32 board."""
import asyncio
import os
import time
from typing import Callable

from websockets.asyncio.client import connect

from mac_app.audio import AudioError, OpusDecoder, OpusEncoder
from mac_app.protocol import detect_message, hello_message, listen_message, parse_server_message

HEADERS = {"device-id": "mac-app", "client-id": "mac-app"}
UP_FRAME_BYTES = 1920  # 60 ms of 16 kHz int16 mono


class Session:
    def __init__(self, url, mic_frames: asyncio.Queue, speaker, on_state: Callable[[str], None],
                 idle_timeout: float = 10.0, tail: float = 0.25):
        self._url = url
        self._mic = mic_frames
        self._speaker = speaker
        self._on_state = on_state
        self._idle_timeout = idle_timeout
        self._tail = tail
        self._state = "idle"
        self._mute_until = 0.0
        self._listening_since = 0.0
        self._decoder = None

    def _set(self, state: str):
        self._state = state
        self._on_state(state)

    async def run(self, first_text: str = "hi bibi") -> None:
        self._set("connecting")
        try:
            async with connect(self._url, additional_headers=HEADERS, open_timeout=5, max_size=None) as ws:
                await self._converse(ws, first_text)
        except (OSError, asyncio.TimeoutError, Exception):
            pass  # refused / dropped / protocol error: just return to idle
        finally:
            self._set("idle")

    async def _converse(self, ws, first_text):
        await ws.send(hello_message())
        hello = await asyncio.wait_for(self._wait_hello(ws), 5)
        sid = hello.session_id
        params = hello.audio_params or {}
        self._decoder = OpusDecoder(params.get("sample_rate", 24000), params.get("frame_duration", 60))
        await ws.send(listen_message(sid, "start"))
        await ws.send(detect_message(sid, first_text))
        self._set("thinking")
        tasks = [asyncio.create_task(c) for c in (self._receive(ws, sid), self._send_mic(ws), self._watch_idle())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _wait_hello(self, ws):
        async for raw in ws:
            if isinstance(raw, str):
                ev = parse_server_message(raw)
                if ev.kind == "hello":
                    return ev
        raise ConnectionError("closed before server hello")

    async def _receive(self, ws, sid):
        async for raw in ws:
            if isinstance(raw, bytes):
                try:
                    self._speaker.play(self._decoder.decode(raw))
                except (AudioError, Exception):
                    pass
                continue
            ev = parse_server_message(raw)
            if os.environ.get("MAC_APP_DEBUG") and ev.kind == "tts":
                print(f"[tts] {ev.state} {ev.text!r} (app state {self._state})", flush=True)
            if ev.kind == "stt" and self._state == "listening":
                self._set("thinking")
            elif ev.kind == "tts" and ev.state in ("start", "sentence_start"):
                if ev.state == "sentence_start" and hasattr(self._speaker, "speak"):
                    self._speaker.speak(ev.text)
                if self._state != "speaking":
                    self._set("speaking")
            elif ev.kind == "tts" and ev.state == "stop":
                while hasattr(self._speaker, "speak") and self._speaker.is_busy():
                    await asyncio.sleep(0.1)  # let `say` finish before we listen
                self._mute_until = time.monotonic() + self._tail
                await ws.send(listen_message(sid, "start"))
                self._listening_since = time.monotonic()
                self._set("listening")

    async def _send_mic(self, ws):
        enc = OpusEncoder(16000)
        pending = b""
        sent = skipped = 0
        peak = 0
        last = time.monotonic()
        while True:
            frame = await self._mic.get()
            if os.environ.get("MAC_APP_DEBUG") and time.monotonic() - last > 2:
                print(f"[send] state={self._state} sent={sent} skipped={skipped} peak={peak}", flush=True)
                peak = 0
                sent = skipped = 0
                last = time.monotonic()
            if self._speaker.is_busy():
                self._mute_until = time.monotonic() + self._tail
            if self._state != "listening" or time.monotonic() < self._mute_until:
                pending = b""
                skipped += 1
                continue
            sent += 1
            pending += frame
            peak = max(peak, int(max(abs(x) for x in memoryview(frame).cast('h')[::8])))
            if len(pending) >= UP_FRAME_BYTES:
                chunk, pending = pending[:UP_FRAME_BYTES], pending[UP_FRAME_BYTES:]
                await ws.send(enc.encode(chunk))

    async def _watch_idle(self):
        while True:
            await asyncio.sleep(0.05)
            if (self._state == "listening" and not self._speaker.is_busy()
                    and time.monotonic() - max(self._listening_since, self._mute_until) > self._idle_timeout):
                return
