"""Pytest configuration — must run BEFORE any test imports project modules.

Adds the xiaozhi-server directory to sys.path so the existing implicit
relative imports (`from core.utils.textUtils import ...`) resolve.

External-API mock toggle (default: mocked)
-------------------------------------------
The dep-upgrade orchestrator and CI run this test suite *without* any
real API credentials. By default the tests construct the actual provider
objects from ``data/.config.yaml`` but route every network call through
a recording shim, so no traffic leaves the host.

Set ``RUN_LIVE_API_TESTS=1`` to opt in to real network calls. In that
mode the 5-module readiness check runs in strict form (any missing key
→ ``sys.exit(3)``), the mock shims are uninstalled, and tests hit the
real providers.

Configuration loading
---------------------
Always uses the file-merge path (``config.yaml`` + ``data/.config.yaml``).
``manager-api`` is intentionally NOT contacted — pytest must work in a
single-module deployment.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import yaml

# The Windows console defaults to GBK; printing emoji / CJK text would crash, so force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# opuslib_next stub: keeps the import chain working; real audio calls fail, tests handle that with try/except
# ---------------------------------------------------------------------------
try:
    import opuslib_next  # noqa: F401
except Exception as _opus_import_error:
    # opuslib_next is a ctypes binding; the pip package is pure Python and the real codec relies on the
    # system libopus.so. When libopus is not installed on the runner the import fails, so a stub stands in here.
    # The stub only keeps the import chain working; for real encoding/decoding libopus must be installed first
    # (apt-get install libopus0, which is also how the production image Dockerfile-server-base installs it).
    print(
        f"[conftest] opuslib_next unavailable, using a stub: {_opus_import_error}",
        flush=True,
    )

    def _opus_unavailable(_self, *_args, **_kwargs):
        raise RuntimeError(
            "libopus is not installed, Opus encoding/decoding cannot run. "
            "Install: apt-get install libopus0 (the production image Dockerfile-server-base installs it the same way)"
        )

    _stub = types.ModuleType("opuslib_next")
    _stub.Encoder = type(
        "Encoder", (), {"__init__": lambda *a, **kw: None, "encode": _opus_unavailable}
    )
    _stub.Decoder = type(
        "Decoder", (), {"__init__": lambda *a, **kw: None, "decode": _opus_unavailable}
    )
    _stub.APPLICATION_AUDIO = 0
    _stub.OpusError = type("OpusError", (Exception,), {})
    _stub.constants = types.SimpleNamespace()
    sys.modules["opuslib_next"] = _stub
    for sub in ("api", "exceptions"):
        sys.modules[f"opuslib_next.{sub}"] = types.ModuleType(f"opuslib_next.{sub}")


# ---------------------------------------------------------------------------
# Config loading: always merge from files
# ---------------------------------------------------------------------------
# Priority:
#   1. The file named by the environment variable ``PYTEST_CONFIG_FILE`` (recommended - isolated test config)
#   2. data/.config.yaml (production / single-module deployment config)
_DEFAULT_CUSTOM = _PROJECT_ROOT / "data" / ".config.yaml"
_CUSTOM_CONFIG = Path(os.environ["PYTEST_CONFIG_FILE"]) if os.environ.get(
    "PYTEST_CONFIG_FILE"
) else _DEFAULT_CUSTOM


def _load_via_file_merge() -> dict:
    """Pure file merge: config.yaml + data/.config.yaml. Same as a production single-module deployment."""
    from config.config_loader import merge_configs

    config: dict = {}
    default_path = _PROJECT_ROOT / "config.yaml"
    if default_path.exists():
        config = yaml.safe_load(default_path.read_text(encoding="utf-8")) or {}

    if _CUSTOM_CONFIG.exists():
        custom = yaml.safe_load(_CUSTOM_CONFIG.read_text(encoding="utf-8")) or {}
        config = merge_configs(config, custom)

    return config


# Always merge from files: use the single-module config directly
CONFIG: dict = _load_via_file_merge()


# ---------------------------------------------------------------------------
# Key: replace the runtime config entry point ``load_config`` with a function that
# returns the CONFIG we already built, so ``config.config_loader.load_config`` does not
# re-read ``data/.config.yaml`` while the tests run.
# ---------------------------------------------------------------------------
async def _load_config_patched(*_args, **_kwargs):
    return CONFIG


import config.config_loader as _cl  # noqa: E402
_cl.load_config = _load_config_patched


# ---------------------------------------------------------------------------
# Config access helpers (used by the tests)
# ---------------------------------------------------------------------------
def get_provider_config(module: str, provider_name: str | None = None) -> dict:
    """Get the config dict of a provider.

    - provider_name=None: use ``selected_module[module]``
    - otherwise the one given explicitly
    Returns {} when it is not configured.
    """
    if provider_name is None:
        provider_name = CONFIG.get("selected_module", {}).get(module)
    if not provider_name:
        return {}
    return CONFIG.get(module, {}).get(provider_name, {}) or {}


def get_selected_provider_name(module: str) -> str | None:
    return CONFIG.get("selected_module", {}).get(module)


def get_provider_type(module: str, provider_name: str | None = None) -> str | None:
    """Return the type name that ``create_instance`` really needs (it may differ from the section name).

    Convention (from core/utils/modules_initialize.py): fall back to the section name when type is missing.
    """
    cfg = get_provider_config(module, provider_name)
    if not cfg:
        return None
    return cfg.get("type") or (provider_name or get_selected_provider_name(module))


def has_real_key(config: dict, *keys: str) -> bool:
    """Any candidate key whose value is not a placeholder counts as a real secret.

    Placeholder rule: empty / "your ..." / contains "placeholder" / literal "none".
    """
    if not config:
        return False
    for key in keys:
        val = config.get(key)
        if val is None:
            continue
        s = str(val).strip().lower()
        if not s or s == "none":
            continue
        if s.startswith("your") or "placeholder" in s:
            continue
        return True
    return False


# ---------------------------------------------------------------------------
# Default mock mode: intercept third-party HTTP/SDK network calls so tests run without real keys
# ---------------------------------------------------------------------------
LIVE_API_TESTS: bool = os.environ.get("RUN_LIVE_API_TESTS") == "1"

# List of third-party SDKs. In live mode nothing is done (real imports are kept).
# In mock mode these names are put into sys.modules, with placeholder exception classes / clients.
_MOCKED_MODULES: tuple[str, ...] = (
    # Note: real third-party packages such as openai / httpx / requests are usually installed in the conda env,
    # and shimming the whole module would break submodules such as ``openai.types`` / ``httpx.AsyncClient``
    # resolution. Shim only when the package is not installed (__getattr__ still takes the real import path).
    "anthropic",
    "edge_tts",
    "elevenlabs",
    "mem0",
    "mem0ai",
    "freezegun",
)

# These packages are shimmed even when installed - they either blow up on import in the local model chain
# (torch pulls in heavy native libraries) or are heavy GPU dependencies that mock mode cannot run.
_FORCE_SHIM_MODULES: tuple[str, ...] = (
    "torch",
    "torchaudio",
)


def _try_import_real(name: str) -> bool:
    """Check whether a real third-party package can be loaded (no real import, only importer.find_spec).

    An explicit ``__import__`` triggers package-level side effects (torch etc.), so bypass it and
    just check whether the package is in sys.path / site-packages.
    """
    import importlib.util

    spec = importlib.util.find_spec(name)
    return spec is not None


class _RecordingShim:
    """A no-op stand-in for any object reachable through a shimmed module.

    Acts as both a callable and an attribute container: every attribute
    access returns a callable that records the call and yields another
    shim. Used as a base class for fake exception classes so
    ``except httpx.ConnectError`` blocks evaluate correctly.
    """

    _calls: list = []

    def __init__(self, *_args, **_kwargs):
        pass

    def __getattr__(self, attr):
        def _record(*args, **kwargs):
            type(self)._calls.append((attr, args, kwargs))
            return _RecordingShim()

        return _record

    def __call__(self, *args, **kwargs):
        type(self)._calls.append(("__call__", args, kwargs))
        return _RecordingShim()


class _ShimModule(types.ModuleType):
    """PEP 562 module — any attribute access returns a recording shim."""

    def __getattr__(self, attr):
        if attr in ("__path__", "__loader__", "__spec__"):
            raise AttributeError(attr)
        # Cache so identity stays stable across accesses.
        value = _RecordingShim()
        setattr(self, attr, value)
        return value


def _install_mock_layer() -> None:
    """Insert PEP 562 shims for every third-party SDK in :data:`_MOCKED_MODULES`.

    Exception classes (e.g. ``httpx.ConnectError``) become ``_RecordingShim``
    subclasses — so ``except`` blocks match and ``isinstance`` checks pass.
    Common callable symbols (``Client`` / ``OpenAI`` / ``AutoModel``) are
    pre-bound to the shim type so ``Client(...)`` constructions succeed.

    If a real package is already importable (e.g. openai installed in conda),
    we leave it alone — shimming would break submodule resolution like
    ``openai.types``.
    """
    for name in list(_MOCKED_MODULES) + list(_FORCE_SHIM_MODULES):
        if name in sys.modules and isinstance(sys.modules[name], _ShimModule):
            continue
        if name not in _FORCE_SHIM_MODULES and _try_import_real(name):
            # Real package is available — use it. We'll patch network I/O
            # at the request layer in mock mode (see below).
            continue
        shim = _ShimModule(name)
        shim.OpenAI = _RecordingShim
        shim.AsyncOpenAI = _RecordingShim
        shim.Client = _RecordingShim
        shim.AsyncClient = _RecordingShim
        shim.AutoModel = _RecordingShim
        shim.completions = _RecordingShim()
        shim.chat = _RecordingShim()
        shim.exceptions = _ShimModule(f"{name}.exceptions")
        sys.modules[name] = shim
        sys.modules[f"{name}.exceptions"] = shim.exceptions

    # Shorten asyncio.sleep so retry loops do not slow the tests down.
    import asyncio

    _real_sleep = asyncio._real_sleep = getattr(asyncio, "_real_sleep", asyncio.sleep)

    async def _fast_sleep(_seconds, result=None):
        # Must still yield to the event loop: a no-yield sleep turns every
        # `while True: ...; await asyncio.sleep(x)` loop into a hot spin that
        # starves the loop and grows queues without bound (GBs of RAM).
        await _real_sleep(0)
        return result

    asyncio.sleep = _fast_sleep  # type: ignore[assignment]

    # Network-layer mock: if the real httpx is loaded, replace its send with
    # a stub that raises ConnectError immediately, so ``_should_retry`` reaches ``raise``
    # without sending real requests. Live mode does not install this layer at all.
    if not LIVE_API_TESTS:
        try:
            import httpx as _httpx

            async def _fake_async_send(self, request, **kwargs):
                raise _httpx.ConnectError("MOCK: connect refused")

            def _fake_send(self, request, **kwargs):
                raise _httpx.ConnectError("MOCK: connect refused")

            # Replace only the send method, not the whole class. Some libraries (openai 2.x's
            # SyncHttpxClientWrapper) subclass httpx.Client and build requests themselves,
            # and if the whole class were replaced they would inherit from a fake class and hit
            # an AttributeError when building requests, with no sign the mock layer is the cause
            _httpx.AsyncClient.send = _fake_async_send  # type: ignore[assignment]
            _httpx.Client.send = _fake_send  # type: ignore[assignment]
        except ImportError:
            pass


if not LIVE_API_TESTS:
    _install_mock_layer()


# ---------------------------------------------------------------------------
# Live-mode startup check: the single-module deployment check was done early during file loading
# ---------------------------------------------------------------------------
if LIVE_API_TESTS:
    print("[conftest] OK: single-module deployment (file-merged config)")


_REQUIRED_MODULES = ("ASR", "LLM", "TTS", "Memory", "VAD")
_SECRET_FIELDS = (
    "api_key", "token", "access_token", "authorization",
    "secret_key", "access_key", "app_id", "app_secret",
    "bot_id", "user_id",
)


def _check_modules_ready() -> None:
    """Live mode: all 5 core modules must be configured. Mock mode only prints a soft warning."""
    problems: list[str] = []
    selected = CONFIG.get("selected_module") or {}
    for module in _REQUIRED_MODULES:
        provider_name = selected.get(module)
        if not provider_name:
            problems.append(f"{module} is not set in selected_module (dev forgot to select it)")
            continue
        cfg = CONFIG.get(module, {}).get(provider_name)
        if not cfg:
            problems.append(
                f"{module}.{provider_name} config missing (no such provider in the CFG dict)"
            )
            continue
        secret_keys_present = [k for k in _SECRET_FIELDS if k in cfg]
        if secret_keys_present and not any(
            has_real_key(cfg, k) for k in secret_keys_present
        ):
            problems.append(
                f"secret fields of {module}.{provider_name}: "
                f"{secret_keys_present} are all placeholders (dev did not fill in a real key)"
            )
    if not problems:
        return
    if LIVE_API_TESTS:
        msg = (
            "\n[conftest] ============================================================\n"
            "[conftest] FAIL: pytest requires all 5 core modules to be configured.\n"
            "[conftest] Missing / not ready:\n"
            + "\n".join(f"[conftest]   - {p}" for p in problems)
            + "\n[conftest] You must configure all 5 modules in data/.config.yaml"
            " (ASR/LLM/TTS/Memory/VAD) before running pytest.\n"
            "[conftest] ============================================================"
        )
        print(msg, file=sys.stderr, flush=True)
        sys.exit(3)
    print(
        "[conftest] NOTE: default mock mode - the following modules are not fully configured, but pytest can still run:\n  - "
        + "\n  - ".join(problems)
        + "\n[conftest] Live request mode: export RUN_LIVE_API_TESTS=1 and run once everything is configured.",
        flush=True,
    )


_check_modules_ready()


# ---------------------------------------------------------------------------
# Explicit marker: tell the user the current mode in mock mode
# ---------------------------------------------------------------------------
if not LIVE_API_TESTS:
    print(
        "[conftest] MOCK mode (default): third-party HTTP/SDK shims injected, no real requests.\n"
        "[conftest] Live request mode: export RUN_LIVE_API_TESTS=1 pytest tests/ -v",
        flush=True,
    )


import pytest  # noqa: E402  (after conftest hooks so pytest is initialised)


def pytest_collection_modifyitems(config, items):
    """Live mode: tests marked ``live_api`` need all keys configured to run (otherwise skip)."""
    if LIVE_API_TESTS:
        return
    # Mock mode does not force a skip: tests should all be able to run (with shims).
    return
