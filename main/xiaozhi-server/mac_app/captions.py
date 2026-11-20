"""Latest caption line for the overlay: what you said ("you") or what vibi is saying ("vibi")."""
import threading
import time

_lock = threading.Lock()
_cur = ("", "", 0.0)  # role, text, monotonic time


def set(role: str, text, append: bool = False) -> None:
    """append=True adds to the current line when it has the same role (a reply spoken in parts)."""
    global _cur
    text = " ".join(str(text or "").split())
    if text:
        with _lock:
            if append and _cur[0] == role and _cur[1]:
                text = _cur[1] + " " + text
            _cur = (role, text, time.monotonic())


def reset() -> None:
    global _cur
    with _lock:
        _cur = ("", "", 0.0)


def current(max_age: float = 12.0):
    """(role, text) while fresh, else ("", "")."""
    with _lock:
        role, text, ts = _cur
    return (role, text) if text and time.monotonic() - ts < max_age else ("", "")
