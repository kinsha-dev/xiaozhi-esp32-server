"""Local wake-phrase detection: VAD segmentation + a small Whisper model + fuzzy phrase match."""
import re
from typing import Callable, Optional

import numpy as np

RATE = 16000
FRAME_MS = 30
END_SILENCE_FRAMES = 20  # 600 ms of silence closes a segment
MIN_SPEECH_FRAMES = 8  # shorter blips are noise
MAX_SEGMENT_S = 3

_GREET = {"hi", "hey", "hay", "high", "hai"}
_NAMES = {"bibi", "vibi", "vivi", "bebe", "bibbi", "vibe", "bibie", "vibie", "bibby", "bubi"}
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


class WakeDetector:
    def __init__(self, transcribe: Callable[[np.ndarray], str], phrase: str = "hi bibi", vad=None):
        self._transcribe = transcribe
        self._phrase = phrase
        if vad is None:
            import webrtcvad

            vad = webrtcvad.Vad(2)
        self._vad = vad
        self._reset()

    def _reset(self):
        self._frames = []
        self._speech = 0
        self._silence = 0

    def feed(self, frame: bytes) -> bool:
        """Feed one 30 ms 16 kHz int16 mono frame. True once when a finished segment matches the phrase."""
        if self._vad.is_speech(frame, RATE):
            self._frames.append(frame)
            self._speech += 1
            self._silence = 0
            return False
        if not self._frames:
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
        return matches_wake(self._transcribe(audio), self._phrase)


_model = None


def whisper_transcriber(model: str = "tiny.en") -> Callable[[np.ndarray], str]:
    def transcribe(audio: np.ndarray) -> str:
        global _model
        if _model is None:
            import whisper

            _model = whisper.load_model(model)
        return _model.transcribe(audio, fp16=False, language="en", condition_on_previous_text=False)["text"]

    return transcribe
