# mac_control plugin

Voice control of Claude Code and the Mac from the ESP32 board. Module: `mac_control.py`.
Enable by adding `mac_control` to `Intent.function_call.functions` in `data/.config.yaml`.

| Tool | What it does |
|---|---|
| `ask_pi(task, allow_changes=False)` | Runs the desk's sandboxed `pi` agent (via `voice-note-box/server/pi_ask.js`) in `/Users/kinsha/Spx_Trading_Signal` with `read` + `bash` tools, 145 s timeout, max 20 runs/day. The macOS sandbox lets it write inside the project folder only and **denies deleting project files** (`rm`, `find -delete`, `node -e`, `git clean` all fail). Full answer saved to `data/pi_tasks/<timestamp>.md`; speaks two short sentences. No Claude CLI dependency. |
| `ask_claude(task, allow_changes=False)` | Same idea with the Claude CLI (`claude -p`), used when the user says "ask Claude". Tools `Read,Grep,Glob,Bash`; Bash is allowed but deletion is denied by rule (`rm`, `rmdir`, `unlink`, `find -delete`, `git clean`, `sudo`, shells). 120 s timeout. Answers saved to `data/claude_tasks/`. Needs a fresh Claude login. |
| `mac_control(action, arg)` | `set_volume` (0-100), `mute`, `play_pause`, `lock_screen`, `open_app`, `run_shortcut`. |
| `confirm_action()` | Runs the newest pending risky action once the user has said yes. Single-use, expires after 60 s; a new request replaces the old one. |

**Confirmation (critical actions only).** Only tasks that edit live files (`ask_pi`/`ask_claude` with changes) and `run_shortcut` ask for a spoken yes. `lock_screen`, WhatsApp reading and voice builds run immediately. Tasks that look like they edit/run things (or `allow_changes=true`), `lock_screen` and
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

Module `whatsapp_reader.py`; tools `whatsapp_messages` and `clear_whatsapp_messages` ("vibi clear messages": marks all
unread WhatsApp messages as read and runs `vibi clear` to drop queued Claude messages, without speaking them). Enable by adding `whatsapp_messages` to
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
4. Add `whatsapp_messages` and `clear_whatsapp_messages` to `Intent.function_call.functions` and restart the server.

If the inbox is missing or older than 5 minutes the assistant says the collector isn't running.

Flow: "any new WhatsApp messages?" -> the device says how many are waiting and reads up to 5 messages as sender plus an 80-character preview and marks them read ("And N more"
for the rest). Message text is never in the text returned to the LLM. Read flags are kept in `data/whatsapp_buffer.json` (mode 600). It cannot send, reply or delete. The notification-database layout is undocumented
by Apple and may change between macOS versions; the plugin degrades to a spoken "can't read" message.

Tests: `PYTHONPATH=. python -m unittest tests.test_whatsapp_reader`.

## feature_builder plugin (voice-started background builds)

Module `feature_builder.py`; tools `build_feature(request, project, followup=False)` and
`build_status(action)`. Enable by adding `build_feature` and `build_status` to `Intent.function_call.functions`.

- "Ask Claude to build a timer command for the voice box" -> the build starts immediately (no spoken yes: the work
  is isolated on its own branch and nothing is pushed or merged).
- The build runs the Claude CLI (read, bash, edit, write; `git push`, `git merge`, deletion, sudo and shells denied)
  in a new git worktree `data/builds/<project>_<time>` on a new branch `voice/<time>-<slug>`. One build at a time,
  30 min limit (`FEATURE_BUILD_TIMEOUT`). Any project known to `mac_control` can be built (`trading`, `voicebox`, `server`); the safeguards above apply to all.
- Follow-ups: `build_status` -> "status" (still running?), "result" (what it built and the branch to review),
  "cancel". `build_feature(followup=true)` continues the latest build's branch with further changes. When a build ends
  it queues a spoken note through `voice-note-box/bin/vibi say` if that exists, so "any messages?" reports it.
- Merging is manual: review the branch, then `git merge voice/...` yourself. If the server restarts mid-build the
  job is reported as lost; the branch keeps whatever was done.

Tests: `PYTHONPATH=. python -m unittest tests.test_feature_builder`.

## Claude Code follow-up hook (post to the voice box)

`scripts/claude_stop_voicebox.py` is a Claude Code `Stop` hook: when a session ends a turn it queues the last message
(two plain-speech sentences, no code or paths) with `vibi say`, so the board speaks it now or on the next "Hi ESP".
It only fires for sessions under `VIBI_HOOK_DIRS` (colon separated; default: Spx_Trading_Signal, voice-note-box,
xiaozhi-server and the builds folder), skips hook loops, and always exits 0. Add to `~/.claude/settings.json`
(or a project `.claude/settings.json`) under `hooks`:

```json
"Stop": [{"hooks": [{"type": "command", "command": "/usr/bin/python3 /Users/kinsha/esp/xiaozhi-server/main/xiaozhi-server/scripts/claude_stop_voicebox.py"}]}]
```

## github_search plugin (voice GitHub search)

Module `github_search.py`; tool `github_search(query, kind, owner, open_top)`. Enable by adding `github_search` to
`Intent.function_call.functions`. Uses the Mac's signed-in `gh` CLI (`gh search repos|issues|prs|code`, read-only,
argument list, query passed after `--`). "Search GitHub for esp32 voice assistant" -> count plus the top three;
"...and open it" opens the best match in the default browser, only when it is an `https://github.com/` address.
`gh search code` and private results use the signed-in account's access. Needs `gh auth status` to be OK.

Tests: `PYTHONPATH=. python -m unittest tests.test_github_search`.

## browser_task plugin (jev-ultrafast browser use)

Module `browser_task.py`; tool `browser_task(url, goal)`. Enable by adding `browser_task` to
`Intent.function_call.functions` (needs `confirm_action` too). Uses a pinned clone of
[browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast) (MIT) at
`~/esp/xiaozhi-local/vendor/jev-ultrafast` (commit 8251e27, installed with `uv sync`, `uv` from Homebrew).

Setup: copy `.env.example` to `.env` there (mode 600) and fill `TEXT_MODEL_API_KEY` (the OpenRouter key), `TEXT_MODEL` (text helper) and optionally `JEV_CHOOSER_MODEL`
(the action chooser: `typesafe/jev-1.13` goes to OpenRouter's `/api/alpha/decisions` endpoint, ~5 s for the Wikipedia
test; any chat model id also works through a slower translation, ~11 s). No TypeSafe account is needed:
`scripts/jev_openrouter_run.py` routes jev's per-step decision to OpenRouter. Chrome must allow remote debugging (`browser-harness --doctor`,
`browser-harness mac-approve` for the macOS sheet). Browser Harness telemetry is switched off (opt-out stored in
`~/.config/browser-harness` and `BH_TELEMETRY=0` on every run).

"Open the Wikipedia article about Godel" -> the board names the site and goal and waits for a spoken yes; the agent
then works in its own background tab of your Chrome (same signed-in profile) for at most 120 s and speaks where it
ended. Page text goes to openrouter.ai only. Only public https sites; goals mentioning log in, pay,
buy, send, post, delete, book, submit, download and similar are refused. Limits of jev: no iframes, canvas, uploads or
popup tabs.

Tests: `PYTHONPATH=. python -m unittest tests.test_browser_task`.

## web plugin (one tool for all web access)

Module `web.py`; tool `web(mode, query, url, kind, owner, open_top)` with modes `answer` (search_web), `read`
(read_page), `open` (open_in_browser), `github` (github_search) and `browser` (browser_task, no confirmation: the owner-voice gate is the check).
Enable only `web` and `confirm_action` in `Intent.function_call.functions` and remove the individual tools from that
list, so the model has one choice and cannot mis-route "search ..." to the browser agent. The individual modules stay
installed; `web` calls them.

Tests: `PYTHONPATH=. python -m unittest tests.test_web`.
