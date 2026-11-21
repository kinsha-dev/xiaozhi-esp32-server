"""Memory pipeline tests.

Mock mode (default): build the real provider from the data/.config.yaml configuration; external
SDK calls (mem0ai client) go through the shim and no real request is sent.
Live mode (``RUN_LIVE_API_TESTS=1``): call the real Memory provider.

Interface convention (core/providers/memory/base.py):
    async save_memory(msgs, session_id=None) -> str
    async query_memory(query: str) -> str

Default selected_module.Memory = nomem (memory off, local only).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from tests.conftest import (
    get_provider_config,
    get_provider_type,
    get_selected_provider_name,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _build_provider():
    name = get_selected_provider_name("Memory")
    if not name:
        pytest.skip("selected_module.Memory is not set")
    cfg = get_provider_config("Memory", name)
    if not cfg:
        pytest.skip(f"Memory provider '{name}' configuration missing")
    ptype = get_provider_type("Memory", name)
    if not ptype:
        pytest.skip(f"Memory '{name}' type field missing")
    from core.utils.memory import create_instance

    return create_instance(ptype, cfg, None)


def test_memory_provider_constructs_from_config() -> None:
    """The config should instantiate a MemoryProviderBase subclass."""
    provider = _build_provider()
    from core.providers.memory.base import MemoryProviderBase

    assert isinstance(provider, MemoryProviderBase), (
        f"Wrong Memory provider type: {type(provider).__name__}"
    )


def test_memory_provider_exposes_save_and_query() -> None:
    """The provider must implement save_memory / query_memory."""
    provider = _build_provider()
    assert hasattr(provider, "save_memory"), (
        f"{type(provider).__name__} is missing the save_memory method"
    )
    assert hasattr(provider, "query_memory"), (
        f"{type(provider).__name__} is missing the query_memory method"
    )


def test_memory_save_and_query_roundtrip() -> None:
    """query after save should not raise; nomem returning None/empty string both pass."""
    provider = _build_provider()
    msgs = [{"role": "user", "content": "test message"}]

    async def run():
        mid = await provider.save_memory(msgs, "test-session")
        result = await provider.query_memory("test")
        return mid, result

    mid, result = asyncio.run(run())
    # "memory off" implementations such as nomem may return None; a real memory service should return a str ID
    assert mid is None or isinstance(mid, str), (
        f"save_memory should return str or None, got {type(mid).__name__}"
    )
    assert isinstance(result, str), "query_memory should return str"
