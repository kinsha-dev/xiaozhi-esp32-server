# vibi Mac app

Menu-bar client for the xiaozhi server: the Mac's mic and speakers instead of the ESP32 board.
Say **"Hi bibi"** (also "hey vibi", "hi bee bee"); wake detection runs locally (VAD + Whisper `small`),
nothing leaves the Mac before the phrase. `vibi say` / reminders open a session on their own within ~4 s.

Setup (server venv): `pip install -r mac_app/requirements.txt` (openai-whisper, webrtcvad, opuslib_next are
already there). Start the server, then `scripts/mac_app.sh start` (or `python -m mac_app`). Allow the
microphone when macOS asks. Menu: Mute, Retry microphone, Start at Login, Quit.

Env: `MAC_APP_SERVER` (default `ws://127.0.0.1:8000/xiaozhi/v1/`), `MAC_APP_WAKE` (`hi bibi`),
`VOICE_BOX_DATA` (server `data/`), `MAC_APP_SPEAKER_RATE` (`24000`).

Tests: `python -m pytest mac_app/tests` (kept out of `tests/`: its conftest stubs `asyncio.sleep`).

## Vibi Ctrl remote access (tunnel) and passkeys

Menu: **Remote tunnel: off/on** (click to start/stop; shows the hostname) and **Copy remote link**.
The remote listener accepts a passkey login or the shared token (`data/ctrl_token`).

- **Register once**: start the tunnel, *Copy remote link* (token link while token login is on), open it in a
  browser on the device that holds the passkey, then use the **Add passkey** box (bottom right). Next time open
  the plain URL and sign in with Touch ID / phone. Credentials: `data/ctrl_passkeys.json` (0600), user
  verification required, sign count tracked.
- **Token fallback**: stays on until you press *Token login: on* in that box (only possible once a passkey exists
  for this hostname). A hostname with no passkey always accepts the token, so a new hostname can never lock you
  out. To reset from the Mac: delete `data/ctrl_passkeys.json`.
- **Hostname limit**: a passkey is bound to the hostname (the relying-party id). Quick tunnels get a new
  `*.trycloudflare.com` name on every start, so a passkey only works until the tunnel restarts; then register
  again via the token link. For a stable hostname use a named tunnel (needs a Cloudflare account + a domain):
  `cloudflared tunnel login && cloudflared tunnel create vibi-ctrl && cloudflared tunnel route dns vibi-ctrl ctrl.example.com`,
  then write `data/ctrl_tunnel_named.json` = `{"name": "vibi-ctrl", "hostname": "ctrl.example.com"}`.
  The tunnel then starts as `cloudflared tunnel run` on that hostname and passkeys survive restarts.
- Passkey sessions live in memory: restarting the Mac app logs remote browsers out.
