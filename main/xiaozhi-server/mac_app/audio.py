"""Mic capture, speaker playback and Opus codec for the Mac app."""
import subprocess
import threading
from typing import Callable

import numpy as np
import opuslib_next
import sounddevice as sd


class AudioError(Exception):
    pass


class OpusEncoder:
    def __init__(self, rate: int = 16000):
        self._rate = rate
        self._enc = opuslib_next.Encoder(rate, 1, opuslib_next.APPLICATION_AUDIO)

    def encode(self, pcm: bytes) -> bytes:
        """Encode one frame of int16 mono PCM (60 ms = 1920 bytes at 16 kHz)."""
        return self._enc.encode(pcm, len(pcm) // 2)


class OpusDecoder:
    def __init__(self, rate: int, frame_ms: int = 60):
        self._frame = rate * frame_ms // 1000
        self._dec = opuslib_next.Decoder(rate, 1)

    def decode(self, opus: bytes) -> bytes:
        return self._dec.decode(opus, self._frame)


class Mic:
    """16 kHz int16 mono capture; calls on_frame(bytes) from the audio thread for every frame_ms."""

    def __init__(self, on_frame: Callable[[bytes], None], frame_ms: int = 30):
        self._on_frame = on_frame
        self._block = 16000 * frame_ms // 1000
        self._stream = None

    def start(self):
        try:
            sd.query_devices(kind="input")
            self._stream = sd.RawInputStream(
                samplerate=16000, channels=1, dtype="int16", blocksize=self._block,
                callback=lambda data, frames, t, status: self._on_frame(bytes(data)),
            )
            self._stream.start()
        except Exception as e:  # PortAudioError, ValueError (no device), permission denied
            self._stream = None
            raise AudioError(f"microphone unavailable: {e}") from e

    def stop(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None


class Speaker:
    """Plays int16 mono PCM at `rate`; play() queues, flush() drops what is still queued."""

    def __init__(self, rate: int):
        self._rate = rate
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._stream = None
        self._level = 0.0

    def _callback(self, outdata, frames, t, status):
        need = frames * 2
        with self._lock:
            chunk = bytes(self._buf[:need])
            del self._buf[:need]
        if chunk:
            a = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
            self._level = float(np.sqrt(np.mean(a * a)))
        else:
            self._level = 0.0
        outdata[:] = chunk + bytes(need - len(chunk))

    def play(self, pcm: bytes):
        with self._lock:
            self._buf += pcm
        if self._stream is None:
            try:
                self._stream = sd.RawOutputStream(
                    samplerate=self._rate, channels=1, dtype="int16", callback=self._callback)
                self._stream.start()
            except Exception as e:
                self._stream = None
                raise AudioError(f"speaker unavailable: {e}") from e

    def flush(self):
        with self._lock:
            self._buf.clear()

    def level(self) -> float:
        return self._level

    def is_busy(self) -> bool:
        with self._lock:
            return len(self._buf) > 0


class SaySpeaker:
    """Speaks the server's reply text with the macOS `say` voice (MAC_APP_VOICE picks one; default is the system
    voice) instead of playing the server's TTS audio, which is ignored."""

    def __init__(self, voice: str = ""):
        self._voice = voice
        self._lock = threading.Lock()
        self._queue = []
        self._proc = None
        self._worker = None

    def play(self, pcm: bytes):  # server audio is dropped on purpose
        pass

    def speak(self, text: str):
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            self._queue.append(text)
            if self._worker is None:
                self._worker = threading.Thread(target=self._run, daemon=True)
                self._worker.start()

    def _run(self):
        while True:
            with self._lock:
                if not self._queue:
                    self._worker = None
                    return
                text = self._queue.pop(0)
            cmd = ["say"] + (["-v", self._voice] if self._voice else []) + ["--", text]
            try:
                self._proc = subprocess.Popen(cmd)
                self._proc.wait()
            except OSError:
                pass
            finally:
                self._proc = None

    def flush(self):
        with self._lock:
            self._queue.clear()
        if self._proc is not None:
            self._proc.terminate()

    def is_busy(self) -> bool:
        with self._lock:
            return bool(self._queue) or self._worker is not None

    def level(self) -> float:
        return 0.12 if self.is_busy() else 0.0
