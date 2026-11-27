"""Vibi Ctrl's Chat tab: log each reply as it joins the dialogue (data/history.jsonl, kind "reply")."""
import re

LIMIT = 4000
_CJK = re.compile(r"[぀-ヿ㐀-鿿]")  # the server's own Chinese warm-up lines are not conversation
# spoken confirm codes and passwords must not sit in a log the panel shows
_REDACT = [
    (re.compile(r"\b(password|passcode|passphrase|pin|api key|token|secret)\b(\s+(is|was|:))?\s*[^\s,.;]+", re.I), r"\1 [redacted]"),
    (re.compile(r"\b(confirm(ation)?( code)?)(\s+(is|was))?[\s:]*[\d][\d\s,.-]*\d", re.I), r"\1 [redacted]"),
]


def log_reply(message) -> None:
    """Never raises: a log failure must not break a voice turn."""
    try:
        text = message.content
        if message.role != "assistant" or message.is_temporary or not isinstance(text, str):
            return
        text = text.strip()
        if not text or _CJK.search(text):
            return
        for pattern, repl in _REDACT:
            text = pattern.sub(repl, text)
        from plugins_func.functions import history_log as hl

        hl.record_async("reply", status="ok", text=text[:LIMIT])
    except Exception:
        pass
