# Mac menu-bar app (vibi without the ESP32) - design

## Intent
Use the existing voice assistant (xiaozhi server + Whisper owner-voice gate + memory + all voice tools: mac_control,
browser, web, WhatsApp, notes/reminders, github, `vibi` outbox/inbox) using only the Mac's mic and speakers. The ESP32-S3
board becomes optional. Why: no hardware to carry/power, and the Mac can be always-on so `vibi say` and reminders speak
immediately instead of waiting for the board to wake.

## Goals
- Menu-bar app (no Dock icon) that listens for the wake phrase **"Hi bibi"**, then holds a conversation with the server.
- Reuses the server unchanged: speaks the same websocket protocol the board does (`ws://localhost:8000`, hello / listen /
  Opus audio up, TTS audio down, abort/stop), so every plugin, the memory and the owner-voice gate keep working.
- Menu shows state (idle / listening / thinking / speaking), Mute, Start at Login, Quit.
- Spoken output for `vibi say` / reminders plays immediately when idle (drain the same outbox the board uses).
- Runs from the server's existing venv; install/start scripts alongside the existing launchd ones.

## Non-goals
Firmware, OTA, board provisioning, Wi-Fi setup, a new server, Windows/Linux, a signed/notarized .app.

## Anti-goals (failure even if it "works")
- Background audio (YouTube, calls) triggering the assistant or being sent off-device unintentionally.
- Mic audio leaving the Mac before the wake phrase is detected (wake detection is fully local).
- High idle CPU/battery drain from wake detection.
- Breaking the board path: the server must keep working with the ESP32 and the Mac app at the same time.

## Constraints
- Python 3.12 venv already used by the server; Apple Silicon macOS; mic permission prompt on first run.
- Wake phrase must be configurable (default "Hi bibi"; accept near-misses like "hi vibi"/"hi bee bee").

## Decisions
User decided: menu-bar app, wake-word activation, phrase "Hi bibi".
My calls (not agreed with user - flag for review):
- Python + `rumps` (menu bar), `sounddevice` (audio), `opuslib` (Opus), `websockets`; new folder `voice-note-box/mac-app/`.
- Wake detection: local voice-activity segmentation + a small local Whisper model matching the phrase fuzzily, because
  openWakeWord/Porcupine need training or a key for a custom phrase. Swappable behind a small `WakeDetector` interface.
- Echo: mute wake detection and mic upload while the app itself is speaking; allow barge-in only via the wake phrase.
- Conversation ends on server "stop"/goodbye intent or after a silence timeout (match board behaviour).

## Left to the builder
Exact protocol message shapes (read the server's websocket handlers and the board firmware), audio frame sizes,
VAD thresholds, packaging of launchd plist, tests (unit-test wake matching and protocol framing; manual mic test).
