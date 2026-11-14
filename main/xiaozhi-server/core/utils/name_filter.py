"""Board only answers speech that says its name, or that follows a recent exchange.

Background audio (TV, podcasts, other people) almost never says "Vibi", so a sentence without the name is dropped before the
LLM sees it. After Vibi accepts a turn, a short follow-up window lets a natural reply ("and tomorrow?") through without
repeating the name; so does a question Vibi just asked (data/awaiting_reply_until).
"""
import re
import time

from core.confirmation_window import _file as _awaiting_file

FOLLOWUP_S = 25  # after an accepted turn: long enough to hear the answer and reply
# The ways "vibi" is really heard; ordinary words ("baby", "vibe") are left out on purpose.
_NAME = re.compile(r"\b(vibi|vibie|vibey|vibby|vivi|vivie|bibi|bibie|bibbi|bibby|vb|v\.? ?b\.?|vee ?bee|vee ?vee|bee ?bee)\b", re.I)


def says_name(text: str) -> bool:
    return bool(_NAME.search(text or ""))


def _awaiting(now: float) -> bool:
    try:
        return float(_awaiting_file().read_text()) > now
    except (OSError, ValueError):
        return False


def should_answer(text: str, followup_until: float = 0.0, now: float = None) -> bool:
    now = time.time() if now is None else now
    return says_name(text) or now < followup_until or _awaiting(now)
