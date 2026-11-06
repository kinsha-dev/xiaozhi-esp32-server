"""Watches the voice-box outbox (data/outbox.jsonl, written by `vibi say`, reminders, ...)."""
import asyncio
import time
import json
from pathlib import Path
from typing import Callable


RETRY_S = 20.0


def has_pending(path: Path) -> bool:
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            if json.loads(line).get("status") == "pending":
                return True
        except (ValueError, AttributeError):
            continue
    return False


async def watch(path: Path, on_pending: Callable[[], None], interval: float = 4.0, stop: asyncio.Event = None):
    """Call on_pending() each time the outbox goes from no pending rows to some, and again every RETRY_S while rows stay
    pending (a session that died, e.g. when the server restarted, must not leave them stuck)."""
    stop = stop or asyncio.Event()
    was = False
    last = 0.0
    while not stop.is_set():
        now = has_pending(path)
        if now and (not was or time.monotonic() - last >= RETRY_S):
            on_pending()
            last = time.monotonic()
        was = now
        try:
            await asyncio.wait_for(stop.wait(), interval)
        except asyncio.TimeoutError:
            pass
