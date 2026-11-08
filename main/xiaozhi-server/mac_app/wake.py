"""Local wake-phrase detection: VAD segmentation + a small Whisper model + fuzzy phrase match."""
import os
import re
from collections import deque
from typing import Callable, Optional

import numpy as np

RATE = 16000
FRAME_MS = 30
END_SILENCE_FRAMES = 20  # 600 ms of silence closes a segment
MIN_SPEECH_FRAMES = 8  # shorter blips are noise
MAX_SEGMENT_S = 8  # room for "hi vibi" plus a short command
MIN_PEAK = 0.03  # quieter than this is room noise; do not spend Whisper time on it
PREROLL_FRAMES = 10  # VAD fires late; keep 300 ms before onset so "Hi" isn't clipped

_GREET = {"hi", "hey", "hai"}
# Only the ways "vibi" is really misheard. Ordinary words ("baby", "bobby", "vibe") are left out: songs and videos woke the app.
_NAMES = {"bibi", "vibi", "vivi", "bibbi", "bibie", "vibie", "bibby", "vb"}
_PAIRS = {("bee", "bee"), ("vee", "vee"), ("be", "be"), ("bi", "bi"), ("vi", "vi")}


def _tokens(text: str):
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).split()


def matches_wake(text: str, phrase: str = "hi bibi") -> bool:
    """True if `text` *starts with* a greeting plus the assistant's name (Whisper often misspells it)."""
    toks = _tokens(text)
    if len(toks) < 2 or toks[0] not in _GREET:
        return False
    names = _NAMES | set(_tokens(phrase)[1:])
    if toks[1] in names:
        return True
    return len(toks) >= 3 and (toks[1], toks[2]) in _PAIRS


def wake_command(text: str, phrase: str = "hi bibi") -> str:
    """What was said after the wake phrase in the same breath ("hi vibi what time is it" -> "what time is it")."""
    toks = _tokens(text)
    if not matches_wake(text, phrase):
        return ""
    names = _NAMES | set(_tokens(phrase)[1:])
    used = 2 if toks[1] in names else 3
    return " ".join(toks[used:])


class WakeDetector:
    def __init__(self, transcribe: Callable[[np.ndarray], str], phrase: str = "hi bibi", vad=None):
        self._transcribe = transcribe
        self._phrase = phrase
        if vad is None:
            import webrtcvad

            vad = webrtcvad.Vad(2)
        self._vad = vad
        self.command = ""  # text spoken after the wake phrase in the segment that last matched
        self._reset()

    def _reset(self):
        self._frames = []
        self._preroll = deque(maxlen=PREROLL_FRAMES)
        self._speech = 0
        self._silence = 0

    def feed(self, frame: bytes) -> bool:
        """Feed one 30 ms 16 kHz int16 mono frame. True once when a finished segment matches the phrase."""
        if self._vad.is_speech(frame, RATE):
            if not self._frames:
                self._frames.extend(self._preroll)
            self._frames.append(frame)
            self._speech += 1
            self._silence = 0
            return False
        if not self._frames:
            self._preroll.append(frame)
            return False
        self._frames.append(frame)
        self._silence += 1
        if self._silence < END_SILENCE_FRAMES:
            return False
        frames, speech = self._frames, self._speech
        self._reset()
        if speech < MIN_SPEECH_FRAMES:
            return False
        pcm = np.frombuffer(b"".join(frames), dtype=np.int16)[-MAX_SEGMENT_S * RATE:]
        audio = pcm.astype(np.float32) / 32768.0
        peak = float(np.abs(audio).max()) if len(audio) else 0.0
        if os.environ.get("MAC_APP_DEBUG"):
            print(f"[wake] segment {len(audio) / RATE:.1f}s peak={peak:.3f}", flush=True)
        if peak < MIN_PEAK:
            return False
        if peak < 0.5:  # quiet laptop mic: bring speech up to a level Whisper handles well
            audio = audio * (0.5 / peak)
        heard = self._transcribe(audio)
        self.command = wake_command(heard, self._phrase)
        return matches_wake(heard, self._phrase)


_model = None


def whisper_transcriber(model: str = "") -> Callable[[np.ndarray], str]:
    def transcribe(audio: np.ndarray) -> str:
        global _model
        if _model is None:
            import torch
            import whisper

            # Only "Hi bibi" has to be recognised, so a small model and few threads are enough; "small" on every
            # noise segment kept the whole Mac at 250%+ CPU.
            torch.set_num_threads(int(os.environ.get("MAC_APP_WAKE_THREADS", "2")))
            _model = whisper.load_model(model or os.environ.get("MAC_APP_WAKE_MODEL", "base"))
        return _model.transcribe(audio, fp16=False, language="en", condition_on_previous_text=False,
                                  initial_prompt="Hi bibi. Hey bibi.")["text"]

    return transcribe
