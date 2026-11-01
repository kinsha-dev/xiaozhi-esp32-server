# vibi Mac app

Menu-bar client for the xiaozhi server: the Mac's mic and speakers instead of the ESP32 board.
Say **"Hi bibi"** (also "hey vibi", "hi bee bee"); wake detection runs locally (VAD + Whisper `tiny`),
nothing leaves the Mac before the phrase. `vibi say` / reminders open a session on their own within ~4 s.

Setup (server venv): `pip install -r mac_app/requirements.txt` (openai-whisper, webrtcvad, opuslib_next are
already there). Start the server, then `scripts/mac_app.sh start` (or `python -m mac_app`). Allow the
microphone when macOS asks. Menu: Mute, Retry microphone, Start at Login, Quit.

Env: `MAC_APP_SERVER` (default `ws://127.0.0.1:8000/xiaozhi/v1/`), `MAC_APP_WAKE` (`hi bibi`),
`VOICE_BOX_DATA` (server `data/`), `MAC_APP_SPEAKER_RATE` (`24000`).

Tests: `python -m pytest mac_app/tests` (kept out of `tests/`: its conftest stubs `asyncio.sleep`).
