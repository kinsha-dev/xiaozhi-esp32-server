"""One conversation with the xiaozhi server, speaking the same websocket protocol as the ESP32 board."""
import asyncio
import os
import time
from pathlib import Path
from typing import Callable

from websockets.asyncio.client import connect

from mac_app.audio import AudioError, OpusDecoder, OpusEncoder
from mac_app.protocol import detect_message, hello_message, listen_message, parse_server_message
from mac_app import captions

HEADERS = {"device-id": "mac-app", "client-id": "mac-app"}
UP_FRAME_BYTES = 1920  # 60 ms of 16 kHz int16 mono
CONNECT_S = 30.0  # a busy Mac can take well over 5 s to answer; giving up sooner drops the command
AUTO_REPLY_IDLE_S = 20.0  # an unprompted read-out that asked a question waits this long for the answer


def awaiting_reply() -> bool:
    """True while the server's last spoken sentence was a question (data/awaiting_reply_until)."""
    f = Path(os.environ.get("VOICE_BOX_DATA", Path.home() / "esp/voice-note-box/data")) / "awaiting_reply_until"
    try:
        return time.time() < float(f.read_text().strip())
    except (OSError, ValueError):
        return False


class Session:
    def __init__(self, url, mic_frames: asyncio.Queue, speaker, on_state: Callable[[str], None],
                 idle_timeout: float = 10.0, tail: float = 0.25, connect_timeout: float = CONNECT_S):
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
        self._auto = False
        self.quiet = False  # Stealth: drop audio, never speak, never listen; the text reply is kept in `reply`
        self.reply = []
        self.connect_timeout = connect_timeout
        self.sent = False  # the command reached the server
        self.error = ""  # why the session failed, in words for the user
        self._stopped = False  # a reply finished

    def _set(self, state: str):
        self._state = state
        self._on_state(state)

    async def run(self, first_text: str = "hi bibi", auto: bool = False) -> None:
        """auto=True: Vibi started this by itself (a queued update). It ends after speaking, unless it asked a
        question, so room or TV speech is never taken as a command."""
        self._auto = auto
        if auto:
            self._idle_timeout = min(self._idle_timeout, AUTO_REPLY_IDLE_S)
        self._set("connecting")
        try:
            async with connect(self._url, additional_headers=HEADERS, open_timeout=self.connect_timeout, max_size=None) as ws:
                await self._converse(ws, first_text)
        except (TimeoutError, asyncio.TimeoutError):
            self._failed(f"the voice server did not answer within {self.connect_timeout:g} s (the Mac may be overloaded)")
        except OSError as e:
            self._failed(f"could not reach the voice server ({e.strerror or type(e).__name__})")
        except Exception as e:  # dropped / protocol error: return to idle, but say why
            self._failed(f"the connection to the voice server failed ({type(e).__name__}: {str(e)[:120]})")
        finally:
            self._set("idle")

    def _failed(self, why: str):
        self.error = f"Sent, but {why} before replying." if self.sent else f"Nothing ran: {why}, so the command was not sent."
        print(f"[session] {self.error}", flush=True)

    async def _converse(self, ws, first_text):
        await ws.send(hello_message())
        hello = await asyncio.wait_for(self._wait_hello(ws), self.connect_timeout)
        sid = hello.session_id
        params = hello.audio_params or {}
        self._decoder = OpusDecoder(params.get("sample_rate", 24000), params.get("frame_duration", 60))
        await ws.send(listen_message(sid, "start"))
        await ws.send(detect_message(sid, first_text))
        self.sent = True
        captions.set("you", first_text)
        self._set("thinking")
        tasks = [asyncio.create_task(c) for c in (self._receive(ws, sid), self._send_mic(ws), self._watch_idle())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            broke = next((t.exception() for t in done if t.exception()), None)
            if not self.reply and (broke or tasks[0] in done and not self._stopped):
                self._failed("the voice server closed the connection")
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
                if self.quiet:
                    continue
                try:
                    self._speaker.play(self._decoder.decode(raw))
                except (AudioError, Exception):
                    pass
                continue
            ev = parse_server_message(raw)
            if os.environ.get("MAC_APP_DEBUG") and ev.kind == "tts":
                print(f"[tts] {ev.state} {ev.text!r} (app state {self._state})", flush=True)
            if ev.kind == "stt":
                captions.set("you", ev.text)
            if ev.kind == "stt" and self._state == "listening":
                self._set("thinking")
            elif ev.kind == "tts" and ev.state in ("start", "sentence_start"):
                if ev.state == "start":
                    captions.reset()  # a new reply: start its caption fresh
                if ev.state == "sentence_start":
                    captions.set("vibi", ev.text, append=True)
                    self.reply.append(ev.text)
                if ev.state == "sentence_start" and not self.quiet and hasattr(self._speaker, "speak"):
                    self._speaker.speak(ev.text)
                if self._state != "speaking":
                    self._set("speaking")
            elif ev.kind == "tts" and ev.state == "stop":
                self._stopped = True
                captions.finish()
                if self.quiet:
                    return  # nothing was spoken, so nothing to wait for, and no listening in Stealth
                while hasattr(self._speaker, "speak") and self._speaker.is_busy():
                    await asyncio.sleep(0.1)  # let `say` finish before we listen
                self._mute_until = time.monotonic() + self._tail
                if self._auto and not awaiting_reply():
                    return  # read-out done and nothing was asked: close, listen only for "hi vibi" again
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
