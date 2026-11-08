# Bluetooth LE fallback for the voice board — design

## Intent
The board (Waveshare ESP32-S3 Audio Board, firmware `~/esp/xiaozhi-esp32`) normally talks to the server over Wi-Fi + WebSocket.
When Wi-Fi is not available for a long time, the user still wants to speak to Vibi and hear the spoken reply.
Fallback: the board talks to the Mac over Bluetooth LE; a relay on the Mac forwards everything to the unchanged server WebSocket.

## Behaviour (decided by the user)
- Trigger: no Wi-Fi connection for **10 minutes** (continuous). Then the board enables BLE and connects to the Mac.
- While in fallback the board sends a **heartbeat** over BLE (so the Mac menu bar does not show X).
- Voice commands go board -> BLE -> Mac relay -> server WebSocket. Replies (the spoken audio, and the JSON control messages) come back
  server -> relay -> BLE -> board, so the answer plays **on the board**.
- Nothing changes while Wi-Fi works.

## Decided by me (user can overrule)
- The board is the BLE peripheral (GATT server); the Mac relay is the central (Python + `bleak`).
- The board keeps retrying Wi-Fi while in fallback and drops BLE and returns to the WebSocket path by itself when Wi-Fi comes back.
- The relay runs inside the signed `~/Applications/Vibi.app` (macOS refuses Bluetooth access to plain launchd Python, same as the microphone).
- The relay presents itself to the server as the board (same device-id header and hello message), so the server needs no change.
- Wire format over BLE: the existing 4-byte `BinaryProtocol3` framing (type, reserved, length, payload; type 0 = Opus, 1 = JSON),
  fragmented to the negotiated BLE MTU, over one write characteristic (board -> Mac) and one notify characteristic (Mac -> board).
- Audio stays Opus, 16 kHz, 60 ms frames (about 16-24 kbps), which fits BLE 5 with the 2M PHY and a raised MTU.

## Goals
- Spoken round trip works with Wi-Fi off, after the 10-minute wait.
- Automatic switch both ways, no button press.
- Heartbeat visible to the menu bar during fallback.

## Non-goals
- WebSocket directly over Bluetooth (not possible: Bluetooth has no IP here).
- Bluetooth Classic (the ESP32-S3 has none).
- Faster-than-10-minute fallback, multiple boards, or Bluetooth when Wi-Fi works.
- Any server change beyond what is needed to accept the relay as the board.

## Anti-goals (failure even if it "works")
- BLE turned on while Wi-Fi is fine (power, radio coexistence, glitches).
- Garbled or choppy audio; replies cut off.
- Breaking the existing Wi-Fi path or the home Wi-Fi settings saved in the board.
- Two sessions of the board at once (Wi-Fi and BLE both open).

## Constraints
- Bluetooth is **not enabled** in the current firmware build (no `CONFIG_BT_*`); needs NimBLE enabled, flash and RAM checked, then a reflash over USB (`/dev/cu.usbmodem1101`).
- Mac Bluetooth permission needs the `Vibi.app` wrapper with a Bluetooth usage description in its Info.plist.
- The Mac must be within BLE range of the board (about 10 m).
- Wi-Fi and BLE share the radio on the S3; coexistence must be verified, not assumed.

## Left to the builder
- NimBLE vs Bluedroid choice (NimBLE expected), UUIDs, MTU/connection-interval tuning, pairing/bonding approach (the board should only accept the user's Mac).
- How the firmware's `Protocol` class gets a BLE implementation next to `websocket_protocol.cc` (a new `ble_protocol.cc`) and how `Application` chooses it.
- Where the relay lives (`mac_app/` module or sibling) and how it is started with the menu-bar app.
- Test approach: a fake board (BLE peripheral on the Mac) or loopback tests for framing/fragmentation; a manual test with Wi-Fi off.

## Risks
- BLE throughput under real conditions may force a lower Opus bitrate or larger frames.
- Flash/RAM headroom on the board for the BLE stack.
- macOS BLE central quirks (permission prompts, reconnect after sleep).
