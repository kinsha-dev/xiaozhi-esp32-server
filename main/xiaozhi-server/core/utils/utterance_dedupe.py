"""One spoken command, one answer, when the board and the Mac app are both listening.

Both devices hear the same sentence and each sends it to the server, usually seconds apart (the board
can lag by a minute when the Mac is busy). The first connection to accept a command answers it; the
other drops a near-identical command that arrives within WINDOW_S."""
import re
import threading
import time

WINDOW_S = 90
MIN_OVERLAP = 0.6
_lock = threading.Lock()
_recent = []  # (timestamp, token set, connection id)


def _tokens(text):
    return set(re.findall(r"[a-z0-9']+", (text or "").lower()))


def is_duplicate(text, conn_id, now=None):
    """True if another connection already took a near-identical command; otherwise record this one."""
    now = time.time() if now is None else now
    toks = _tokens(text)
    if len(toks) < 2:
        return False
    with _lock:
        _recent[:] = [r for r in _recent if now - r[0] <= WINDOW_S]
        for _, other, cid in _recent:
            if cid != conn_id and len(toks & other) / max(1, min(len(toks), len(other))) >= MIN_OVERLAP:
                return True
        _recent.append((now, toks, conn_id))
    return False
