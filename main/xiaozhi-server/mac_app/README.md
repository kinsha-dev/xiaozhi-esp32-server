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

## Vibi Ctrl: Todo tab

Lists, adds, completes, reopens and deletes todos, and picks up changes every 2 s. The list is `data/todos.jsonl` in the
voice box repo, read and written through its `server/todo_store.py` (shared with the voice tools). Ways in:

- **Voice** (board or Mac): "add a todo ...", "what's on my todo list", "mark the todo ... done". Setup, once the voice
  box repo has `server/plugin/todos.py`: link it with
  `ln -s ~/esp/voice-note-box/server/plugin/todos.py plugins_func/functions/todos.py`, add `todos` under
  `Intent.function_call.functions` in `data/.config.yaml`, and restart the voice server. (`scripts/install.sh` there
  also makes the link.)
- **Chat tab:** send to *Todo list*, type a todo command to *Vibi*, or start a message to Claude / Grok / Gemini with
  `todo:`. These are handled by the panel itself and answered at once.

Tests: `mac_app/tests/test_todo_tab.py` (`VIBI_TODO_STORE` points it at another voice box checkout).

## Chat tab: one timeline

The Message board tab is merged into **Chat**: every conversation and feed (Claude, Grok Bot, Gemini, Computer use,
Vibi, Todo list, job hunt, notifications, WhatsApp, mail digest, other apps) in time order. Filter by message type
("Conversations" = the first six), direction (to or from Vibi) and time range (last 72 hours or all time); the newest
300 matches are shown and the choice is remembered. Long feed items start collapsed. Old `#board` links open Chat.

**Actions** (the former Actions tab) thread under the voice or typed command they ran for, with status, details,
"asks first" and Rerun; actions run for no command (schedules, tasks, CLI) stand on their own. Old `#actions` links open
Chat. The floating **Filter** button holds type (including "Actions"), action, voice / typed, direction and time.

**Paste:** ⌘V (or the 📋 Paste button) puts text in the message box; a screenshot, image or copied file becomes the
attachment (one, 5 MB max). Files can't go to Computer use or the Todo list.

**Progress and failures:** a typed command shows "Waiting for Vibi…" until it is answered, a computer task shows its step
count, and its result arrives as a Computer use entry. When nothing comes back the entry says why (for example the voice
server did not answer within 30 s, so nothing ran); a reply is never left empty.

**Commands** (the former Commands tab): each command entry has **Read** and, for voice and typed commands, **Rerun**.
Read shows the command, its status and result, and reads them aloud in the browser you are using (silent when Stealth is
on; the board does not speak). Rerun replays it as a voice request, asks first when it is risky, and Vibi's spoken yes
still applies. Notes to Claude have Read only. The "Commands" type lists them all; old `#commands` links open Chat that
way, with every action state and all time.

**Action: recognized** (the default) shows voice commands that were acted on: the server recorded that the speech went to
the model or ran a tool, and a tool ran or Vibi gave a real reply. **Unrecognized** shows the rest: ignored or misheard
speech, "didn't catch that" replies (matched on wording), and wake / bye chatter, with any actions they ran. **All** shows
both. Typed turns and non-voice rows always count as recognized.

**Voice and typed turns** are in the timeline too, labelled and filterable: what Vibi heard or you typed, its replies,
announcements and reminders. They come from `data/history.jsonl`, `data/memory/conversations.jsonl` and the outbox.
Replies appear as they are given once the voice server runs this version (it logs `reply` rows); before that, only when a
conversation ends. Not recorded: audio that is not put into the dialogue (music, TV and tone playback, some error or
filler lines), what the board or Mac says on its own without the server, and replies in Chinese. Replies from before
this version exist only for conversations the memory kept (at least one user turn and one other).

## Replying to one response

In **Chat**, click a Claude / Grok / Gemini / Vibi response (or its **↩ Reply**) to open the reply
menu: the quoted response, a text box, and a recipient (Claude, Grok Bot, Gemini or Computer use; preselected to the sender, Claude for
Vibi). The reply goes through `/api/chat` with up to 1000 characters of the quote in front as context. Enter sends,
Shift+Enter adds a line, Escape cancels.

- **Claude** only acts when a session is listening (`vibi listen`); otherwise the panel says so and the message waits.
- **Computer use** gets your text and the first link in the quote (not the rest of it), up to 280 characters, through the
  same path as a spoken "use the computer to …", with its spoken yes for risky tasks.

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

## Vibi.app and macOS permissions

macOS grants Microphone, Bluetooth, Accessibility and Full Disk Access per app bundle, so run the app as **Vibi.app**, never from Terminal.
`mac_app/build_app.sh` builds `~/Applications/Vibi.app`: a small C launcher (`scripts/vibi_launcher.c`) that spawns
`.venv/bin/python -m mac_app` from this checkout as a child of the bundle, so macOS attributes the child's access to Vibi
(id `com.voicebox.vibi`, `LSUIElement`, usage-description keys). A full PyInstaller/py2app bundle was not needed: it would
have to freeze torch/whisper/webrtcvad/opuslib/rumps (GBs, fragile) for the same isolation.

- Build from the checkout that should run: `mac_app/build_app.sh` (`MAC_APP_PYTHON`, `APP_DEST` override). It builds in a temp dir, verifies the signature, then swaps.
- Signing is ad-hoc with a fixed designated requirement (`identifier "com.voicebox.vibi"`), so permissions granted once survive rebuilds.
- Grant once in System Settings > Privacy & Security: Microphone, Bluetooth, Accessibility (hotkeys + computer use input), Screen & System Audio Recording (computer use screenshots), Automation (computer use / notifications), and Full Disk Access if needed, to **Vibi** only. Accessibility, Screen Recording and Full Disk Access have no Info.plist key; macOS prompts or you add Vibi.app by hand with +.
- Restart after a rebuild: kill the running mac_app python pid (`open -W` keeps the old one), then `launchctl kickstart -k gui/$(id -u)/com.voicebox.macapp`.
