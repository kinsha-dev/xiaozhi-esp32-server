from mac_app import ctrl_backend as be

PY = "/x/Python.app/Contents/MacOS/Python /Users/u/esp/voice-note-box/bin/vibi listen"


def test_listener_for_this_project_counts():
    assert be.listener_in([PY + " --project voice box --session abc"])
    assert be.listener_in([PY + " --project voice-note-box"])
    assert be.listener_in([PY + " --wait 86400"])  # project comes from its cwd: give it the benefit of the doubt


def test_other_projects_and_shell_wrappers_do_not_count():
    assert not be.listener_in([])
    assert not be.listener_in([PY + " --project Spx_Trading_Signal --session abc"])
    assert not be.listener_in(["/bin/zsh -c eval '~/esp/voice-note-box/bin/vibi listen --project \"voice box\"'"])


def test_chat_to_claude_says_when_nobody_is_listening(tmp_path, monkeypatch):
    monkeypatch.setattr(be, "claude_listening", lambda: False)
    r = be.chat(tmp_path, "claude", "open the pr")
    assert "no Claude session is listening" in r["note"]
    assert "open the pr" in (tmp_path / "to_claude.jsonl").read_text()
    monkeypatch.setattr(be, "claude_listening", lambda: True)
    assert "note" not in be.chat(tmp_path, "claude", "again")


def test_chat_to_claude_calls_the_wake_launcher(tmp_path, monkeypatch):
    client = tmp_path / "client.py"
    client.write_text("calls = []\n"
                      "def notify(data_dir, row, origin):\n"
                      "    (data_dir / 'called').write_text(origin + ' ' + row['text'])\n"
                      "    return {'status': 'queued'}\n")
    monkeypatch.setenv("VIBI_WAKE_CLIENT", str(client))
    monkeypatch.setattr(be, "claude_listening", lambda: False)
    assert "starting Claude" in be.chat(tmp_path, "claude", "open the pr")["note"]
    assert (tmp_path / "called").read_text() == "vibi-ctrl open the pr"
    monkeypatch.setattr(be, "claude_listening", lambda: True)
    assert "note" not in be.chat(tmp_path, "claude", "again")


def test_without_the_launcher_the_old_note_stays(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBI_WAKE_CLIENT", str(tmp_path / "missing.py"))
    monkeypatch.setattr(be, "claude_listening", lambda: False)
    assert be.chat(tmp_path, "claude", "hi")["note"] == be.NOT_LISTENING
