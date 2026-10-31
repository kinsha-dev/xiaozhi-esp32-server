# mac_control plugin

Voice control of Claude Code and the Mac from the ESP32 board. Module: `mac_control.py`.
Enable by adding `mac_control` to `Intent.function_call.functions` in `data/.config.yaml`.

| Tool | What it does |
|---|---|
| `ask_pi(task, allow_changes=False)` | Runs the desk's sandboxed `pi` agent (via `voice-note-box/server/pi_ask.js`) in `/Users/kinsha/Spx_Trading_Signal` with `read` + `bash` tools, 145 s timeout, max 20 runs/day. The macOS sandbox lets it write inside the project folder only and **denies deleting project files** (`rm`, `find -delete`, `node -e`, `git clean` all fail). Full answer saved to `data/pi_tasks/<timestamp>.md`; speaks two short sentences. No Claude CLI dependency. |
| `ask_claude(task, allow_changes=False)` | Same idea with the Claude CLI (`claude -p`), used when the user says "ask Claude". Tools `Read,Grep,Glob,Bash`; Bash is allowed but deletion is denied by rule (`rm`, `rmdir`, `unlink`, `find -delete`, `git clean`, `sudo`, shells). 120 s timeout. Answers saved to `data/claude_tasks/`. Needs a fresh Claude login. |
| `mac_control(action, arg)` | `set_volume` (0-100), `mute`, `play_pause`, `lock_screen`, `open_app`, `run_shortcut`. |
| `confirm_action(token)` | Runs the pending risky action. Tokens: 4 digits, single-use, expire after 60 s. |

**Confirmation.** Tasks that look like they edit/run things (or `allow_changes=true`), `lock_screen` and
`run_shortcut` do nothing on the first call. The device speaks a 4-digit code; the user says "confirm 4 8 2 1".
The code is only in the spoken text, not in the text returned to the LLM. Confirmed Claude tasks may use
pi's `read,bash,edit,write` tools, still under the no-delete sandbox. Overwriting a file in place (`>`, `mv` over it) is still possible.

**Allowlist.** `data/control_allowlist.json` (created with an example if missing):
`{"apps": ["Safari", ...], "shortcuts": []}`. `open_app` and `run_shortcut` only accept exact
(case-insensitive) names listed there. No tool uses a shell; all subprocess calls are argument lists.

Note: `lock_screen` uses `pmset displaysleepnow`, which locks only if "Require password after screen saver/display
off" is set to immediately in macOS settings.

Tests: `PYTHONPATH=. python -m unittest tests.test_mac_control` from `main/xiaozhi-server`.
