# R2D2-style tone responses

## Intent

Vibi currently has two responses to a turn: a full spoken TTS reply, or total
silence (the `[ignore]` marker / `IgnoreGate`, and the board's `name_filter`
gate, both drop background chatter with no feedback at all). Add a third,
lighter-weight response: short synthesized R2D2-ish beep/chirp/whistle tones,
for four situations where a full sentence is too much and silence is too
little.

## Goals

- Four tone "kinds": `confused` (unclear/background input), `confirm`
  (request understood/accepted), `action` (a risky action is being carried
  out), `pending` (a new pending outbox message exists).
- Works on both the board (audio arrives over the websocket, played through
  the ESP32 speaker) and the Mac app (local playback).
- Procedurally synthesized (numpy), cached, no new binary assets checked in.
- Reuses the existing audio-output paths on each side rather than inventing
  a second one.

## Non-goals / anti-goals

- Not a replacement for TTS — tones are additive, short feedback only.
- Not a configurable sound-pack system. Four fixed tones is enough for v1.
- Must not make the board noisier for ordinary background chatter. The
  board's `name_filter` gate exists specifically so ambient noise/TV/other
  people's conversation produce **zero** feedback, audible or otherwise.
  Adding a tone there would defeat the gate's purpose (see Decisions).

## Approaches considered

1. **New dedicated audio-output path per tone** (e.g. a side-channel
   WebSocket message type the firmware has to learn to play). Rejected: it
   needs a firmware change, and the project already has two perfectly good
   playback paths.
2. **Pre-recorded WAV assets** checked into `config/assets/`. Rejected: the
   user explicitly asked for *synthesized* R2D2-style tones (classic
   pitched square/sawtooth sweeps), and procedural generation means no
   binary assets to maintain and trivial pitch/timing tweaks later.
3. **Procedural synthesis + existing playback paths** (chosen): generate
   short int16 PCM tones with numpy (square-wave frequency sweeps with a
   light vibrato, like R2D2's beeps), cache them, and play them through
   whichever output path already exists for that side:
   - **Board**: reuse the exact mechanism `helloHandle.py` already uses for
     cached wakeup-word replies — write the tone to a small cached WAV file
     once, run it through `core.utils.util.audio_to_data()` (same
     file-to-opus-packets helper), and send it with the existing
     `sendAudioMessage(conn, SentenceType.FIRST/LAST, ...)` pipeline. No new
     wire format, no firmware change.
   - **Mac app**: synthesize PCM directly, play it with
     `sounddevice.play()` (a new tiny helper in `mac_app/audio.py`,
     independent of `Speaker`/`SaySpeaker` — those two classes exist to
     play or deliberately drop the *server's* TTS audio; a locally
     generated tone is a different thing and plays regardless of which
     voice backend is configured).

## Decisions (my call, noted here since the brief left them open)

- **Confused/garbled tone fires on:**
  - the board's `IgnoreGate` reaching `ignoring` state (the LLM decided a
    turn was background chatter) — this is a *post-LLM, rarer* signal.
  - the Mac app's `WakeDetector` finishing a segment that had real,
    transcribed speech but didn't match the wake phrase (a "near miss").
  - **Not** the board's `name_filter.should_answer() == False` gate. That
    gate is pre-LLM and fires on *almost every* sentence of ambient audio
    that doesn't say "Vibi" (TV, other people, etc.) — by design, so the
    board can sit in a room without reacting to anything. Making that
    audible would reintroduce exactly the constant noise the gate was
    built to eliminate. Likewise the Mac app's "too quiet" / no-speech
    outcomes (pure room noise) stay silent — only a transcribed near-miss
    gets a tone.
- **Confirm + action tones hook into `confirm_action`'s two-step flow**
  (`plugins_func/functions/mac_control.py`): `confirm` plays the instant the
  user's "yes" is accepted (before the possibly-slow `_run_claude`/`_run_pi`
  dispatch), `action` plays right as the validated action actually starts
  executing. This is the one place in the codebase that already models
  "accepted" and "now doing it" as two distinct moments.
- **Pending tone** is Mac-app only. The board never gets a standalone
  "pending" moment — its outbox items are folded into the next wake-word
  reply's text and spoken normally (`vibi_outbox.wake_text`). The Mac app's
  `Controller.on_pending()` is the one place with a *silent* gap today (the
  muted-notify branch and the "waiting for hi vibi" branch) — pending plays
  there, not when `on_pending()` is about to start a full speaking session
  anyway (that would just be a tone immediately before a sentence).

## What's left to the builder (none — I am the builder here)
