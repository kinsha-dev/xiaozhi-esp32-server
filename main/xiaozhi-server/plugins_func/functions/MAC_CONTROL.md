# mac_control plugin

Voice control of Claude Code and the Mac from the ESP32 board. Module: `mac_control.py`.
Enable by adding `mac_control` to `Intent.function_call.functions` in `data/.config.yaml`.

| Tool | What it does |
|---|---|
| `ask_claude(task, allow_changes=False)` | Runs `claude -p` in `/Users/kinsha/Spx_Trading_Signal`, `Read,Grep,Glob,Bash` (Edit/Write/web denied; Bash allowed but deletion denied: `rm`, `rmdir`, `unlink`, `shred`, `find -delete`, `git clean`, `sudo`, `xargs`, `sh`/`bash`/`eval`), 120 s timeout. Full answer saved to `data/claude_tasks/<timestamp>.md`; speaks two short sentences. |
| `mac_control(action, arg)` | `set_volume` (0-100), `mute`, `play_pause`, `lock_screen`, `open_app`, `run_shortcut`. |
| `confirm_action(token)` | Runs the pending risky action. Tokens: 4 digits, single-use, expire after 60 s. |

**Confirmation.** Tasks that look like they edit/run things (or `allow_changes=true`), `lock_screen` and
`run_shortcut` do nothing on the first call. The device speaks a 4-digit code; the user says "confirm 4 8 2 1".
The code is only in the spoken text, not in the text returned to the LLM. Confirmed Claude tasks may use
Read/Grep/Glob/Bash/Edit/Write, with the same deletion denies. Not a sandbox: `node -e`, `python -c` or `mv` can still destroy data.

**Allowlist.** `data/control_allowlist.json` (created with an example if missing):
`{"apps": ["Safari", ...], "shortcuts": []}`. `open_app` and `run_shortcut` only accept exact
(case-insensitive) names listed there. No tool uses a shell; all subprocess calls are argument lists.

Note: `lock_screen` uses `pmset displaysleepnow`, which locks only if "Require password after screen saver/display
off" is set to immediately in macOS settings.

Tests: `PYTHONPATH=. python -m unittest tests.test_mac_control` from `main/xiaozhi-server`.
