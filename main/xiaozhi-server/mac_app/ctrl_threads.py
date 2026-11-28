"""Chat threads: a reply sits under the conversation it answers.

A reply sent from the Chat tab is text starting "Replying to <who> (<time>):", then the quoted lines ("> ..."), then
what the user typed. The original is the received row from <who> whose text holds the quote. Replies to a reply stay in
the same thread. The original heads the thread, its replies and their answers follow in time order, and the thread sits
where its newest message is. Grok rows also say how the webhook call for their request went.
"""
import json
import re
from pathlib import Path

REPLY = re.compile(r"Replying to (Claude|Grok|Gemini|Vibi) \(([^)\n]*)\):\n((?:>[^\n]*\n)+)([\s\S]*)")
SOURCES = {"Claude": "Claude", "Grok": "Grok Bot", "Gemini": "Gemini", "Vibi": "Vibi"}
WEBHOOK_LOG = "grok/webhook.jsonl"


def _flat(text) -> str:
    return " ".join(str(text or "").split())


def parse_reply(body: str):
    """(who, time, quoted text, what the user typed), or None when `body` is not a reply."""
    m = REPLY.search(body or "")
    if not m:
        return None
    quote = _flat("\n".join(line[1:] for line in m[3].splitlines())).rstrip("…")
    said = re.split(r"\n(?:reason: |Webhook nudge )", m[4])[0].strip()
    return m[1], m[2], quote, said


def _parent(rows, reply, child):
    who, when, quote, _ = reply
    found = [r for r in rows
             if r is not child and r.get("direction") == "to" and r.get("source") == SOURCES[who]
             and (r.get("ts") or 0) <= (child.get("ts") or 0) and quote and quote in _flat(r.get("body"))]
    return max(found, key=lambda r: (r.get("time") == when, r.get("ts") or 0), default=None)


def link(rows: list) -> list:
    """Copies of `rows` where every message of a thread has "thread" (the original's id); replies and their answers also
    have "nest", and a typed reply has "said" (its text without the quote)."""
    rows = [dict(r) for r in rows]
    parents, answered = {}, {}
    for i, r in enumerate(rows):
        reply = parse_reply(r.get("body"))
        if not reply:
            continue
        parent = _parent(rows, reply, r)
        if parent is None:
            continue
        parents[i] = parent
        r["said"] = reply[3]
        if r.get("pair_id"):
            answered[r["pair_id"]] = i
    index = {id(r): i for i, r in enumerate(rows)}
    for i, r in enumerate(rows):  # the answer to a reply belongs under it
        asked = answered.get(r.get("pair_id"))
        if i not in parents and asked is not None:
            parents[i] = rows[asked]
    for i in parents:
        node, seen = rows[i], set()
        while index[id(node)] in parents and id(node) not in seen:
            seen.add(id(node))
            node = parents[index[id(node)]]
        rows[i].update(thread=str(node.get("id")), nest=True)
        node["thread"] = str(node.get("id"))
    return rows


def arrange(shown: list, everything: list) -> list:
    """`shown` (a filtered part of `everything`, newest first) with each thread kept together, newest first: its original
    is pulled in even when a filter left it out."""
    roots = {str(r.get("id")): r for r in everything if r.get("thread") and not r.get("nest")}
    groups = {}
    for n, r in enumerate(shown):
        groups.setdefault(r.get("thread") or f"\0{n}", []).append(r)
    out = []
    for key, members in sorted(groups.items(), key=lambda g: -max(r.get("ts") or 0 for r in g[1])):
        if key in roots:
            replies = sorted((r for r in members if r.get("nest")),
                             key=lambda r: (r.get("ts") or 0, r.get("direction") != "from"))
            # newest first, so the original goes last: the Chat tab draws the list bottom up
            members = replies[::-1] + [roots[key]]
        out.extend(members)
    return out


def webhook_logs(data_dir: Path) -> dict:
    """{request id: "Grok webhook: <result> at <time>"} from the newest log line of each."""
    logs = {}
    try:
        lines = (Path(data_dir) / WEBHOOK_LOG).read_text(encoding="utf-8").splitlines()
    except OSError:
        return logs
    for line in lines:
        try:
            row = json.loads(line)
            logs[str(row["id"])] = f"Grok webhook: {row['result']} at {str(row['ts'])[11:19]}"
        except (ValueError, KeyError, TypeError):
            continue
    return logs
