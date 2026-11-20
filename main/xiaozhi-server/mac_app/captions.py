"""Latest caption line for the overlay: what you said ("you") or what vibi is saying ("vibi")."""
import threading
import time

_lock = threading.Lock()
_cur = ("", "", 0.0)  # role, text, monotonic time


def set(role: str, text) -> None:
    global _cur
    text = " ".join(str(text or "").split())
    if text:
        with _lock:
            _cur = (role, text, time.monotonic())


def current(max_age: float = 12.0):
    """(role, text) while fresh, else ("", "")."""
    with _lock:
        role, text, ts = _cur
    return (role, text) if text and time.monotonic() - ts < max_age else ("", "")
