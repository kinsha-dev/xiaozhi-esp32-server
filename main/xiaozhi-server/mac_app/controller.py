"""App logic without any UI: mic -> wake detector -> Session, outbox -> Session, mute, error state."""
import asyncio
import atexit
import os
import queue
import threading
import time
import numpy as np
from typing import Callable

from mac_app.audio import AudioError
from mac_app.outbox import watch

TAIL_S = 0.25  # ignore the mic this long after our own speech / a session


class Controller:
    def __init__(self, cfg, speaker, detector, mic_factory, session_factory, notify: Callable[[str], None]):
        self.cfg = cfg
        self._speaker = speaker
        self._detector = detector
        self._mic = mic_factory(self._on_mic)
        self._session_factory = session_factory
        self._notify = notify
        self.state = "idle"
        self.muted = False
        self.error = None
        self._active = False
        self._sess_q = None
        self._mute_until = 0.0
        self._lock = threading.Lock()
        self._frames = queue.Queue(maxsize=200)
        self._stop = threading.Event()
        self._loop = asyncio.new_event_loop()
        self._stop_async = None
        self.mic_level = 0.0

    # --- lifecycle ---------------------------------------------------------------------------
    def _flag(self):
        return self.cfg.data_dir / "mac_app_open" if self.cfg.data_dir else None

    def _announce_open(self):
        """Presence flag: the whisper service skips the owner-voice gate while this app is running."""
        f = self._flag()
        if f is None:
            return
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(str(os.getpid()))
            atexit.register(lambda: f.unlink(missing_ok=True))
        except OSError:
            pass

    def start(self):
        self._announce_open()
        threading.Thread(target=self._run_loop, daemon=True).start()
        threading.Thread(target=self._worker, daemon=True).start()
        self._start_mic()

    def stop(self):
        self._stop.set()
        f = self._flag()
        if f is not None:
            f.unlink(missing_ok=True)
        self._mic.stop()
        if self._stop_async is not None:
            self._loop.call_soon_threadsafe(self._stop_async.set)

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._stop_async = asyncio.Event()
        self._loop.create_task(watch(self.cfg.data_dir / "outbox.jsonl", self.on_pending, stop=self._stop_async)) \
            if self.cfg.data_dir else None
        self._loop.run_forever()

    def _start_mic(self):
        try:
            self._mic.start()
            self.error = None
        except AudioError as e:
            self.error = str(e)

    def retry_mic(self):
        if not self.muted:
            self._start_mic()

    def set_muted(self, muted: bool):
        self.muted = muted
        if muted:
            self._mic.stop()
        else:
            self._start_mic()

    def display_state(self) -> str:
        if self.error:
            return "error"
        if self.muted:
            return "muted"
        return self.state

    # --- audio -------------------------------------------------------------------------------
    def _on_mic(self, frame: bytes):  # audio thread
        a = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
        self.mic_level = float(np.sqrt(np.mean(a * a))) if a.size else 0.0
        if self._active and self._sess_q is not None:
            self._loop.call_soon_threadsafe(self._push, frame)
        else:
            try:
                self._frames.put_nowait(frame)
            except queue.Full:
                pass

    def _push(self, frame):
        try:
            self._sess_q.put_nowait(frame)
        except (asyncio.QueueFull, AttributeError):
            pass

    def _worker(self):
        while not self._stop.is_set():
            try:
                frame = self._frames.get(timeout=0.2)
            except queue.Empty:
                continue
            if self.muted or self._active or self._speaker.is_busy() or time.monotonic() < self._mute_until:
                continue
            if self._detector.feed(frame):
                self.start_session("hi bibi")

    # --- sessions ----------------------------------------------------------------------------
    def on_pending(self):
        if self.muted:
            self._notify("vibi has a message (muted)")
        else:
            self.start_session("hi bibi")

    def start_session(self, text: str) -> bool:
        with self._lock:
            if self._active or self.muted:
                return False
            self._active = True
            self._sess_q = asyncio.Queue(maxsize=100)
        asyncio.run_coroutine_threadsafe(self._run_session(text, self._sess_q), self._loop)
        return True

    async def _run_session(self, text, q):
        try:
            await self._session_factory(q, self._set_state).run(text)
        finally:
            self._mute_until = time.monotonic() + TAIL_S
            self._active = False
            self._sess_q = None
            self.state = "idle"

    def _set_state(self, state: str):
        self.state = state
