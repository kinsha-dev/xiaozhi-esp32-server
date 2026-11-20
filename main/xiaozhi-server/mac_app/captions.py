"""Latest caption line for the overlay: what you said ("you") or what vibi is saying ("vibi").

Vibi's reply is revealed at about the speed of speech (so the text roughly follows the voice) instead of appearing a
whole sentence at a time; if the text gets far ahead of the reveal it speeds up to catch up, and finish() (the read-out
ended) shows the rest. What you said is shown at once."""
import threading
import time

CPS = 15.0  # characters per second of speech (~150 words/min)
CATCH_UP = 2.0  # seconds: the reveal never lags the text by more than this
_lock = threading.Lock()
_cur = ("", "", 0.0)  # role, full text, monotonic time of last change
_shown = 0.0  # characters of the vibi text revealed so far
_at = 0.0  # monotonic time of the last reveal step
_done = False


def set(role: str, text, append: bool = False) -> None:
    """append=True adds to the current line when it has the same role (a reply spoken in parts)."""
    global _cur, _shown, _at
    text = " ".join(str(text or "").split())
    if text:
        with _lock:
            if append and _cur[0] == role and _cur[1]:
                text = _cur[1] + " " + text
            else:
                _shown, _at = 0.0, time.monotonic()
            _cur = (role, text, time.monotonic())


def reset() -> None:
    global _cur, _shown, _done
    with _lock:
        _cur, _shown, _done = ("", "", 0.0), 0.0, False


def finish() -> None:
    """The read-out ended: reveal whatever is left."""
    global _done
    with _lock:
        _done = True


def current(max_age: float = 12.0):
    """(role, revealed text) while fresh, else ("", "")."""
    global _shown, _at
    with _lock:
        role, text, ts = _cur
        now = time.monotonic()
        if not text or now - ts >= max_age:
            return ("", "")
        if role != "vibi":
            return role, text
        backlog = len(text) - _shown
        rate = max(CPS, backlog / CATCH_UP) if not _done else 1e6
        _shown = min(float(len(text)), _shown + rate * (now - _at))
        _at = now
        n = int(_shown)
        if n < len(text) and text[n - 1: n + 1].strip() == text[n - 1: n + 1] and " " in text[n:]:
            n = text.index(" ", n)  # reveal whole words
        return role, text[:n].strip()
