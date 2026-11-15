import asyncio

import pytest


@pytest.fixture(autouse=True)
def _real_asyncio_sleep(monkeypatch):
    """tests/conftest.py fakes asyncio.sleep; these timing tests need the real one."""
    real = getattr(asyncio, "_real_sleep", None)
    if real is not None:
        monkeypatch.setattr(asyncio, "sleep", real)
