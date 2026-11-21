"""Watches the voice-box outbox (data/outbox.jsonl, written by `vibi say`, reminders, ...)."""
import asyncio
import time
import json
from pathlib import Path
from typing import Callable


RETRY_S = 20.0
MAX_AGE_S = 12 * 3600  # matches vibi_outbox.MAX_AGE_S: an old stuck "pending" row must not retrigger forever


def has_pending(path: Path) -> bool:
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return False
    now = time.time()
    for line in lines:
        try:
            row = json.loads(line)
            if row.get("status") == "pending" and now - row.get("ts", now) < MAX_AGE_S:
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
