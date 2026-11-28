import json
import time

from mac_app import ctrl_backend as be
from mac_app import ctrl_threads as th


def row(id_, ts, direction, body, source="Grok Bot", pair=""):
    return {"id": id_, "ts": ts, "time": f"t{ts}", "direction": direction, "body": body, "source": source, "pair_id": pair}


ORIGINAL = row("o", 10, "to", "Here are deals.\n\nHeadphones are 999.")
ASK = row("a-req", 20, "from", "task\nReplying to Grok (t10):\n> Here are deals.\n> Headphones are…\nCheck history\n"
          "reason: Vibi Ctrl chat\nWebhook nudge (posted when GROK_WEBHOOK_URL is set): {}", pair="a")
ANSWER = row("a-res", 20, "to", "The lowest was 899.", pair="a")
AGAIN = row("b-req", 30, "from", "task\nReplying to Grok (t20):\n> The lowest was 899.\nRecheck", pair="b")
OTHER = row("x", 25, "to", "Unrelated", source="Claude")
ROWS = [AGAIN, OTHER, ASK, ANSWER, ORIGINAL]  # newest first, as the board gives them


def test_replies_and_answers_join_the_original_thread():
    linked = {r["id"]: r for r in th.link(ROWS)}
    assert [linked[i]["thread"] for i in ("o", "a-req", "a-res", "b-req")] == ["o"] * 4
    assert "nest" not in linked["o"] and all(linked[i]["nest"] for i in ("a-req", "a-res", "b-req"))
    assert linked["a-req"]["said"] == "Check history" and "thread" not in linked["x"]
    assert "thread" not in ASK  # the input rows are left alone


def test_thread_stays_together_under_its_original_and_moves_to_its_newest_message():
    linked = th.link(ROWS)
    chat = [r["id"] for r in th.arrange(linked, linked)][::-1]  # as drawn, top to bottom
    assert chat == ["x", "o", "a-req", "a-res", "b-req"]


def test_original_is_pulled_in_when_a_filter_hides_it():
    linked = th.link(ROWS)
    shown = [r for r in linked if r["id"] == "b-req"]
    assert [r["id"] for r in th.arrange(shown, linked)][::-1] == ["o", "b-req"]


def test_a_reply_without_its_original_stays_on_its_own():
    assert all("thread" not in r for r in th.link([ASK, OTHER]))


def test_webhook_result_is_logged_and_read_back(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(be, "_grok_webhook", lambda: ("", "secret-token"))
    be._nudge_grok(tmp_path, "abc123")
    assert "not sent" in th.webhook_logs(tmp_path)["abc123"]
    assert "secret-token" not in (tmp_path / th.WEBHOOK_LOG).read_text() + capsys.readouterr().out


def test_failed_webhook_is_logged_without_the_address(tmp_path, monkeypatch):
    monkeypatch.setattr(be, "_grok_webhook", lambda: ("http://127.0.0.1:1/private-path", "secret-token"))
    be._nudge_grok(tmp_path, "abc123")
    for _ in range(100):
        if th.webhook_logs(tmp_path):
            break
        time.sleep(0.05)
    text = (tmp_path / th.WEBHOOK_LOG).read_text()
    assert json.loads(text)["result"].startswith("failed") and "private-path" not in text and "secret-token" not in text
