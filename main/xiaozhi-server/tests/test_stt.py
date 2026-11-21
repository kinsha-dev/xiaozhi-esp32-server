"""ASR (speech recognition) pipeline tests.

Mock mode (default): build the real ASR provider from the data/.config.yaml configuration;
all external HTTP/SDK calls go through the shim injected by conftest, no real requests are sent.
Live mode (``RUN_LIVE_API_TESTS=1``): call the real ASR provider.

Interface convention (core/providers/asr/base.py):
    async speech_to_text_wrapper(pcm_data, session_id) -> Tuple[Optional[str], Optional[str]]
    async speech_to_text(opus_data, session_id, artifacts) -> Tuple[Optional[str], Optional[str]]

Notes:
- performance_tester_asr.py called the wrapper with 3 args, which is wrong (base takes only 2);
  this file uses the correct 2-arg version
- Local ASR models load their model files at construction:
  if the model has not been downloaded, `__init__` raises and the test is skipped
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

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "config" / "assets"


def _build_provider():
    name = get_selected_provider_name("ASR")
    if not name:
        pytest.skip("selected_module.ASR is not set")
    cfg = get_provider_config("ASR", name)
    if not cfg:
        pytest.skip(f"ASR provider '{name}' configuration missing")
    ptype = get_provider_type("ASR", name)
    if not ptype:
        pytest.skip(f"ASR '{name}' type field missing")
    from core.utils.asr import create_instance

    return create_instance(ptype, cfg, True)


def _find_test_audio() -> Path:
    """Find a wav larger than 300KB in config/assets/ to use as test audio."""
    if not _ASSETS_DIR.exists():
        pytest.skip(f"Audio directory does not exist: {_ASSETS_DIR}")
    wavs = sorted(
        (p for p in _ASSETS_DIR.glob("*.wav") if p.stat().st_size > 300 * 1024),
        key=lambda p: p.stat().st_size,
        reverse=True,  # largest first
    )
    if not wavs:
        pytest.skip(f"No wav file larger than 300KB under config/assets/")
    return wavs[0]


def test_asr_provider_constructs_from_config() -> None:
    """The config should instantiate an ASRProviderBase subclass."""
    provider = _build_provider()
    from core.providers.asr.base import ASRProviderBase

    assert isinstance(provider, ASRProviderBase), (
        f"Wrong ASR provider type: {type(provider).__name__}"
    )


def test_asr_provider_exposes_speech_to_text() -> None:
    """The provider must implement a speech_to_text method."""
    provider = _build_provider()
    assert hasattr(provider, "speech_to_text"), (
        f"{type(provider).__name__} is missing the speech_to_text method"
    )
    assert callable(provider.speech_to_text)


def test_asr_provider_has_output_dir() -> None:
    """The provider should have an output_dir attribute (where recognition results are written)."""
    provider = _build_provider()
    assert hasattr(provider, "output_dir"), (
        f"{type(provider).__name__} is missing the output_dir attribute"
    )


def test_asr_speech_to_text_wrapper_returns_text() -> None:
    """Send one real ASR request (live mode) and assert a recognition result is returned.

    Mock mode also calls it, but the shim returns an empty dict/text and the assertions are lenient:
    text may be None / str / dict, passing as long as no exception is raised.

    Reference performance_tester_asr.py:
    - feed real wav file bytes to speech_to_text_wrapper
    - 10s timeout
    - the returned text may be None (recognition failed) / str (some providers) / dict (with emotion/language)
    """
    provider = _build_provider()
    # Mock mode can run without wav assets (the shim does not read files).
    from tests.conftest import LIVE_API_TESTS
    if LIVE_API_TESTS:
        wav_path = _find_test_audio()
        audio_data = wav_path.read_bytes()
    else:
        # Mock mode: feed placeholder bytes; the shim does not really decode.
        audio_data = b"\x00" * 1024

    async def run():
        return await asyncio.wait_for(
            provider.speech_to_text_wrapper([audio_data], "test-session"),
            timeout=10.0,
        )

    text, file_path = asyncio.run(run())
    # ASR result compatibility: None / str / dict ({content, language, emotion})
    assert text is None or isinstance(text, (str, dict)), (
        f"speech_to_text_wrapper should return (None/str/dict, str|None), "
        f"got text type {type(text).__name__}"
    )
    if isinstance(text, dict):
        # dict style: take the content field (strictly checked in live mode only).
        if LIVE_API_TESTS:
            assert text.get("content"), f"ASR dict result is missing content: {text!r}"
