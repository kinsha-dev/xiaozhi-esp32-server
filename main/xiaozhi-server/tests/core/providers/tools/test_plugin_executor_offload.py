"""Server-side plugin executor: sync plugin functions run in a thread pool without blocking the event loop; async plugins are awaited as usual; exceptions become ERROR responses."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from core.providers.tools.server_plugins.plugin_executor import ServerPluginExecutor
from plugins_func.register import Action, ActionResponse, ToolType, all_function_registry


def _register(name, func, tool_type):
    item = SimpleNamespace(name=name, func=func, type=tool_type, description={})
    all_function_registry[name] = item
    return item


def test_sync_plugin_runs_off_loop_and_async_plugin_is_awaited():
    conn = SimpleNamespace(config={})

    async def paced_sleep(delay):
        # Cannot time with asyncio.sleep: in mock mode conftest replaces it with a stub that does not yield to the loop,
        # so sleep in the thread pool here, making sure every tick really yields the event loop.
        await asyncio.get_running_loop().run_in_executor(None, time.sleep, delay)

    def slow_sync(conn_arg, entity_id):
        time.sleep(0.3)  # simulate blocking IO
        return ActionResponse(Action.RESPONSE, None, f"{entity_id} opened")

    async def fast_async(question):
        await asyncio.sleep(0)
        return ActionResponse(Action.REQLLM, f"Answer: {question}", None)

    def boom(conn_arg):
        raise RuntimeError("cannot connect")

    _register("t_slow_sync", slow_sync, ToolType.SYSTEM_CTL)
    _register("t_fast_async", fast_async, ToolType.WAIT)
    _register("t_boom", boom, ToolType.SYSTEM_CTL)
    try:
        async def go():
            ex = ServerPluginExecutor(conn)
            ticks = []

            async def ticker():
                while True:
                    ticks.append(time.monotonic())
                    await paced_sleep(0.02)

            t = asyncio.create_task(ticker())
            r = await ex.execute(conn, "t_slow_sync", {"entity_id": "light.living"})
            t.cancel()
            assert r.action == Action.RESPONSE and r.response == "light.living opened"
            assert len(ticks) >= 5                      # the event loop kept running during the 300 ms (not blocked by time.sleep)
            r2 = await ex.execute(conn, "t_fast_async", {"question": "what time"})
            assert r2.action == Action.REQLLM and r2.result == "Answer: what time"
            r3 = await ex.execute(conn, "t_boom", {})
            assert r3.action == Action.ERROR and "cannot connect" in r3.response
            r4 = await ex.execute(conn, "t_missing", {})
            assert r4.action == Action.NOTFOUND
        asyncio.run(go())
    finally:
        for n in ("t_slow_sync", "t_fast_async", "t_boom"):
            all_function_registry.pop(n, None)


def test_conn_passing_rules_and_compat_branches():
    """code 3/4/5 pass conn; WAIT/type=None do not; a sync function returning a coroutine is awaited as a fallback."""
    conn = SimpleNamespace(config={})
    seen = {}

    def prompt_fn(conn_arg):
        seen["prompt"] = conn_arg
        return ActionResponse(Action.RESPONSE, None, "switch role")

    def wait_fn():
        seen["wait"] = True
        return ActionResponse(Action.RESPONSE, None, "wait")

    def no_type_fn():
        seen["no_type"] = True
        return ActionResponse(Action.RESPONSE, None, "no type")

    def sync_returns_coro(conn_arg):
        seen["coro"] = conn_arg

        async def inner():
            return ActionResponse(Action.RESPONSE, None, "coroutine fallback")

        return inner()

    _register("t_prompt", prompt_fn, ToolType.CHANGE_SYS_PROMPT)
    _register("t_wait", wait_fn, ToolType.WAIT)
    _register("t_no_type", no_type_fn, None)
    _register("t_sync_coro", sync_returns_coro, ToolType.SYSTEM_CTL)
    try:
        async def go():
            ex = ServerPluginExecutor(conn)
            r1 = await ex.execute(conn, "t_prompt", {})
            assert seen["prompt"] is conn and r1.response == "switch role"
            r2 = await ex.execute(conn, "t_wait", {})
            assert "wait" in seen and r2.response == "wait"
            r3 = await ex.execute(conn, "t_no_type", {})
            assert "no_type" in seen and r3.response == "no type"
            r4 = await ex.execute(conn, "t_sync_coro", {})
            assert seen["coro"] is conn and r4.response == "coroutine fallback"
        asyncio.run(go())
    finally:
        for n in ("t_prompt", "t_wait", "t_no_type", "t_sync_coro"):
            all_function_registry.pop(n, None)


def test_sync_plugin_uses_dedicated_pool():
    """Sync plugins must run on the dedicated pool, not the asyncio default pool (which DNS resolution etc. share)."""
    import threading

    conn = SimpleNamespace(config={})
    seen = {}

    def sync_fn():
        seen["thread"] = threading.current_thread().name
        return ActionResponse(Action.RESPONSE, None, "ok")

    _register("t_pool", sync_fn, ToolType.WAIT)
    try:
        async def go():
            ex = ServerPluginExecutor(conn)
            r = await ex.execute(conn, "t_pool", {})
            assert r.response == "ok"

        asyncio.run(go())
        assert seen["thread"].startswith("plugin"), seen["thread"]  # thread name prefix of the dedicated pool
    finally:
        all_function_registry.pop("t_pool", None)
