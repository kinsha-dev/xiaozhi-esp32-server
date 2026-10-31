# mac_control plugin

Voice control of Claude Code and the Mac from the ESP32 board. Module: `mac_control.py`.
Enable by adding `mac_control` to `Intent.function_call.functions` in `data/.config.yaml`.

| Tool | What it does |
|---|---|
| `ask_pi(task, allow_changes=False)` | Runs the desk's sandboxed `pi` agent (via `voice-note-box/server/pi_ask.js`) in `/Users/kinsha/Spx_Trading_Signal` with `read` + `bash` tools, 145 s timeout, max 20 runs/day. The macOS sandbox lets it write inside the project folder only and **denies deleting project files** (`rm`, `find -delete`, `node -e`, `git clean` all fail). Full answer saved to `data/pi_tasks/<timestamp>.md`; speaks two short sentences. No Claude CLI dependency. |
| `ask_claude(task, allow_changes=False)` | Same idea with the Claude CLI (`claude -p`), used when the user says "ask Claude". Tools `Read,Grep,Glob,Bash`; Bash is allowed but deletion is denied by rule (`rm`, `rmdir`, `unlink`, `find -delete`, `git clean`, `sudo`, shells). 120 s timeout. Answers saved to `data/claude_tasks/`. Needs a fresh Claude login. |
| `mac_control(action, arg)` | `set_volume` (0-100), `mute`, `play_pause`, `lock_screen`, `open_app`, `run_shortcut`. |
| `confirm_action()` | Runs the newest pending risky action once the user has said yes. Single-use, expires after 60 s; a new request replaces the old one. |

**Confirmation.** Tasks that look like they edit/run things (or `allow_changes=true`), `lock_screen` and
`run_shortcut` do nothing on the first call. The device speaks exactly what it is about to do and asks; the user says "yes, confirm", which makes the model call
`confirm_action`. There is no code, so the gate relies on the model calling it only after a real yes. Confirmed Claude tasks may use
pi's `read,bash,edit,write` tools, still under the no-delete sandbox. Overwriting a file in place (`>`, `mv` over it) is still possible.

**Allowlist.** `data/control_allowlist.json` (created with an example if missing):
`{"apps": ["Safari", ...], "shortcuts": []}`. `open_app` and `run_shortcut` only accept exact
(case-insensitive) names listed there. No tool uses a shell; all subprocess calls are argument lists.

Note: `lock_screen` uses `pmset displaysleepnow`, which locks only if "Require password after screen saver/display
off" is set to immediately in macOS settings.

Tests: `PYTHONPATH=. python -m unittest tests.test_mac_control` from `main/xiaozhi-server`.

## whatsapp_reader plugin (read-only WhatsApp)

Module `whatsapp_reader.py`; tool `whatsapp_messages`. Enable by adding `whatsapp_messages` to
`Intent.function_call.functions` in `data/.config.yaml` (alongside `confirm_action`).

Source: the notifications WhatsApp Desktop already shows. No WhatsApp login, no network, no unofficial API.
`scripts/whatsapp_collector.py` (stdlib only, read-only on the macOS notification database, which lives in the
protected `~/Library/Group Containers/group.com.apple.usernoted/db2/db`) copies them into
`data/whatsapp_inbox.json` (mode 600, last 24 h, 50 messages). The server reads only that file, so **the server
needs no Full Disk Access**; only the collector's interpreter does. macOS has no per-folder grant for this database.

Setup:
1. Needs WhatsApp Desktop signed in, notifications on, previews shown (hidden previews are skipped).
2. Grant Full Disk Access to `/usr/bin/python3` (System Settings > Privacy & Security > Full Disk Access, "+", press
   Cmd+Shift+G, type `/usr/bin/python3`). This lets any script run by that interpreter read protected data, so run
   nothing else with it that you don't trust.
3. Run the collector at login with a LaunchAgent, `~/Library/LaunchAgents/local.xiaozhi.whatsapp-collector.plist`:
   ```xml
   <?xml version="1.0" encoding="UTF-8"?>
   <plist version="1.0"><dict>
     <key>Label</key><string>local.xiaozhi.whatsapp-collector</string>
     <key>ProgramArguments</key><array>
       <string>/usr/bin/python3</string>
       <string>/Users/kinsha/esp/xiaozhi-server/main/xiaozhi-server/scripts/whatsapp_collector.py</string>
     </array>
     <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
     <key>StandardErrorPath</key><string>/tmp/whatsapp_collector.err</string>
   </dict></plist>
   ```
   then `launchctl load ~/Library/LaunchAgents/local.xiaozhi.whatsapp-collector.plist`.
4. Add `whatsapp_messages` to `Intent.function_call.functions` and restart the server.

If the inbox is missing or older than 5 minutes the assistant says the collector isn't running.

Flow: "any new WhatsApp messages?" -> the device says only how many are waiting and asks for a spoken yes; on
"yes, confirm" it reads up to 5 messages as sender plus an 80-character preview and marks them read ("And N more"
for the rest). Message text is never in the text returned to the LLM. Read flags are kept in `data/whatsapp_buffer.json` (mode 600). It cannot send, reply or delete. The notification-database layout is undocumented
by Apple and may change between macOS versions; the plugin degrades to a spoken "can't read" message.

Tests: `PYTHONPATH=. python -m unittest tests.test_whatsapp_reader`.
