"""Watches the voice-box outbox (data/outbox.jsonl, written by `vibi say`, reminders, ...)."""
import asyncio
import json
from pathlib import Path
from typing import Callable


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
    """Call on_pending() each time the outbox goes from no pending rows to some."""
    stop = stop or asyncio.Event()
    was = False
    while not stop.is_set():
        now = has_pending(path)
        if now and not was:
            on_pending()
        was = now
        try:
            await asyncio.wait_for(stop.wait(), interval)
        except asyncio.TimeoutError:
            pass
