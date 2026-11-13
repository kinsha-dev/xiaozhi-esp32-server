"""App logic without any UI: mic -> wake detector -> Session, outbox -> Session, mute, error state."""
import asyncio
import atexit
import os
import queue
import threading
import time
import numpy as np
from typing import Callable

from mac_app import earphones
from mac_app.audio import AudioError, refresh_devices
from mac_app.outbox import watch

TAIL_S = 0.25  # ignore the mic this long after our own speech / a session
HEARTBEAT_TIMEOUT = 300  # seconds without a board heartbeat before the menu bar shows X
EARPHONE_CHECK_S = 3.0  # how often to look for other audio playing; the Bluetooth check runs every 5th time


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
        self.earphones_active = False  # Bluetooth headset is the mic and nothing else plays: the Mac app takes over from the board
        self._bt_input = False
        self._earphone_tick = 0

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
        f = self._mute_file()
        if f is not None and f.exists():
            self.muted = True  # Mute survives a restart
        threading.Thread(target=self._run_loop, daemon=True).start()
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._watch_earphones, daemon=True).start()
        if not self.muted:
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
        if self.device_mode() == "board":
            return
        try:
            self._mic.start()
            self.error = None
        except AudioError as e:
            self.error = str(e)

    def retry_mic(self):
        if not self.muted:
            self._start_mic()

    def _mute_file(self):
        return self.cfg.data_dir / "muted" if self.cfg.data_dir else None

    def sync_mute(self, every: float = 1.0):
        """Mute is shared through data/muted so the server (board audio, spoken updates, reminders) obeys it too.
        Picks up a change made elsewhere (panel, CLI); cheap enough to call from the UI timer."""
        now = time.monotonic()
        f = self._mute_file()
        if f is None or now - getattr(self, "_mute_sync", 0.0) < every:
            return
        self._mute_sync = now
        if f.exists() != self.muted:
            self.set_muted(f.exists(), write=False)

    def set_muted(self, muted: bool, write: bool = True):
        self.muted = muted
        f = self._mute_file()
        if write and f is not None:
            try:
                f.write_text("1") if muted else f.unlink(missing_ok=True)
            except OSError:
                pass
        if muted:
            self._mic.stop()
        else:
            self._start_mic()

    def board_ble_linked(self) -> bool:
        """True while this app holds the Bluetooth link to the board (data/board_ble = our pid)."""
        try:
            return int((self.cfg.data_dir / "board_ble").read_text()) == os.getpid()
        except (OSError, ValueError, AttributeError, TypeError):
            return False

    def board_link(self) -> str:
        try:
            m = (self.cfg.data_dir / "board_link").read_text().strip()
        except (OSError, AttributeError, TypeError):
            return "auto"
        return m if m in ("auto", "bluetooth") else "auto"

    def set_board_link(self, mode: str):
        try:
            (self.cfg.data_dir / "board_link").write_text(mode)
        except (OSError, TypeError):
            pass

    def board_connected(self) -> bool:
        """True while the server reports a board connected (data/board_connected = "<server pid> <count>"),
        or the board is linked to this app over Bluetooth."""
        if not self.cfg.data_dir:
            return False
        if self.board_ble_linked():
            return True
        try:
            pid, count = (self.cfg.data_dir / "board_connected").read_text().split()
            os.kill(int(pid), 0)
            return int(count) > 0
        except (OSError, ValueError):
            return False

    # --- audio device: "auto" (board when connected), "mac", "board" -----------------------------
    def device_mode(self) -> str:
        try:
            m = (self.cfg.data_dir / "audio_device").read_text().strip()
        except (OSError, AttributeError, TypeError):
            return "auto"
        return m if m in ("auto", "mac", "board") else "auto"

    def set_device_mode(self, mode: str):
        try:
            (self.cfg.data_dir / "audio_device").write_text(mode)
        except (OSError, TypeError):
            return
        if mode == "board":
            self._mic.stop()
        elif not self.muted:
            self._start_mic()

    def defer_to_board(self) -> bool:
        m = self.device_mode()
        return m == "board" or (m == "auto" and self.board_connected())

    def update_earphones(self, bluetooth_input: bool, other_audio: bool):
        """Earphones take over when a Bluetooth headset is the mic, nothing else plays on the Mac and the board is not
        forced. Returns True when that changed (the mic and speaker then reopen on the new default devices)."""
        active = bluetooth_input and not other_audio and self.device_mode() != "board"
        changed = active != self.earphones_active
        self.earphones_active = active
        return changed

    def _watch_earphones(self):
        while not self._stop.wait(EARPHONE_CHECK_S):
            try:
                if self._earphone_tick % 5 == 0:
                    self._bt_input = earphones.bluetooth_input()
                self._earphone_tick += 1
                other = earphones.other_audio_playing({os.getpid()}) if self._bt_input else False
                if self.update_earphones(self._bt_input, other):
                    self._reopen_audio()
            except Exception:
                pass

    def _reopen_audio(self):
        """The default mic/speaker changed (headset on or off): reopen them so they follow it."""
        self._mic.stop()
        refresh_devices()
        reset = getattr(self._speaker, "reset", None)
        if reset:
            reset()
        if not self.muted:
            self._start_mic()

    @property
    def mirroring(self) -> bool:
        return self.board_state() != "idle"

    def board_state(self) -> str:
        """The board's listening/speaking state while it is the active device, else "idle"."""
        if not self.defer_to_board():
            return "idle"
        try:
            ts, st = (self.cfg.data_dir / "board_state").read_text().split()
            return st if time.time() - float(ts) < 3.0 else "idle"
        except (OSError, ValueError, AttributeError, TypeError):
            return "idle"

    def board_alive(self) -> bool:
        """False when the board has not proved it is alive (data/board_heartbeat) for HEARTBEAT_TIMEOUT seconds."""
        if not self.cfg.data_dir:
            return True
        try:
            return time.time() - float((self.cfg.data_dir / "board_heartbeat").read_text()) < HEARTBEAT_TIMEOUT
        except (OSError, ValueError):
            return False

    def display_state(self) -> str:
        self.sync_mute()
        if self.error:
            return "error"
        if self.device_mode() != "mac" and not self.board_alive() and not self.earphones_active:
            return "offline"
        if self.muted:
            return "muted"
        if self.state == "idle":
            return self.board_state()
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
            if self.muted or self._active or self.defer_to_board() or self._speaker.is_busy() or time.monotonic() < self._mute_until:
                continue
            if self._detector.feed(frame):
                self.start_session(getattr(self._detector, "command", "") or "hi bibi")

    # --- sessions ----------------------------------------------------------------------------
    def auto_speak(self) -> bool:
        """Whether queued updates (Claude, phone notifications) start a session by themselves. Off by default:
        they wait until the user says "hi vibi". Menu toggle, kept in data/mac_auto_speak."""
        try:
            return (self.cfg.data_dir / "mac_auto_speak").read_text().strip() == "1"
        except (OSError, AttributeError, TypeError):
            return False

    def set_auto_speak(self, on: bool):
        try:
            (self.cfg.data_dir / "mac_auto_speak").write_text("1" if on else "0")
        except (OSError, TypeError):
            pass

    def on_pending(self):
        if self.defer_to_board():
            return  # the board speaks it
        if self.muted:
            self._notify("vibi has a message (muted)")
        elif not self.auto_speak():
            return  # waits for "hi vibi"; the server adds the waiting updates to that wake
        else:
            self.start_session("hi bibi", auto=True)

    def start_session(self, text: str, auto: bool = False, force: bool = False) -> bool:
        with self._lock:
            if self._active or self.muted or (self.defer_to_board() and not force):  # force: Vibi Ctrl rerun
                return False
            self._active = True
            self._sess_q = asyncio.Queue(maxsize=100)
        asyncio.run_coroutine_threadsafe(self._run_session(text, self._sess_q, auto), self._loop)
        return True

    async def _run_session(self, text, q, auto=False):
        try:
            session = self._session_factory(q, self._set_state)
            await (session.run(text, auto=True) if auto else session.run(text))
        finally:
            self._mute_until = time.monotonic() + TAIL_S
            self._active = False
            self._sess_q = None
            self.state = "idle"

    def _set_state(self, state: str):
        self.state = state
