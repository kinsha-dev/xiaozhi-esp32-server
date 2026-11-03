import asyncio
import json

from mac_app.outbox import has_pending, watch


def row(status):
    return json.dumps({"id": "a", "status": status, "text": "hi"}) + "\n"


def test_has_pending(tmp_path):
    p = tmp_path / "outbox.jsonl"
    assert not has_pending(p)  # missing file
    p.write_text("")
    assert not has_pending(p)
    p.write_text(row("spoken") + "garbage\n")
    assert not has_pending(p)
    p.write_text(row("spoken") + "garbage\n" + row("pending"))
    assert has_pending(p)


async def test_watch_fires_once_per_transition(tmp_path):
    p = tmp_path / "outbox.jsonl"
    hits, stop = [], asyncio.Event()
    task = asyncio.create_task(watch(p, lambda: hits.append(1), interval=0.02, stop=stop))
    await asyncio.sleep(0.1)
    assert hits == []
    p.write_text(row("pending"))
    await asyncio.sleep(0.15)  # several polls, still one hit
    assert hits == [1]
    p.write_text(row("spoken"))
    await asyncio.sleep(0.1)
    p.write_text(row("pending"))
    await asyncio.sleep(0.1)
    assert hits == [1, 1]
    stop.set()
    await asyncio.wait_for(task, 1)
