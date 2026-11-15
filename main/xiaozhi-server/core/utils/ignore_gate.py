"""Drop a reply that is only a silent-style marker, while letting every other reply stream through untouched.

The prompt tells the model to answer `[ignore]` when the heard speech is background chatter not addressed to
Vibi, and `[ack]` when the whole reply is nothing but a bare acknowledgment ("okay", "got it", "sure") that
isn't worth speaking out loud. A tone covers a bare marker better than a full sentence or dead silence (see
core/utils/tones.py: "confused" for [ignore], "confirm" for [ack]).

`[ignore]` drops the entire reply, including anything the model wrote after it. `[ack]` is a tone only when
it is the whole reply (optional surrounding space or punctuation). `[ack] Done, timer set` is spoken as
"Done, timer set" — the marker is stripped and no tone plays. A marker may arrive split across stream
chunks, so the start of a reply is held back until it is clear whether it is one of the markers or ordinary
text.

The `[ack]` instruction is not in this repo. Add it beside the existing `[ignore]` paragraph in the file
`prompt_template` points at. For this project that is `data/english-prompt.txt` (see
`config.local.example.yaml`); `main/xiaozhi-server/data/` is gitignored, and `agent-base-prompt.txt` does not
mention either marker. Paste:

    If your whole reply would be only a bare acknowledgment with nothing the user needs to hear
    (okay, got it, sure, done), reply with exactly [ack] and no other characters. A short tone plays
    instead of speech. If you have anything else to say, do not emit [ack]; write the reply normally.
"""

IGNORE_MARKER = "[ignore]"
ACK_MARKER = "[ack]"
DEFAULT_MARKERS = {"ignore": IGNORE_MARKER, "ack": ACK_MARKER}
# Leading space or punctuation stuck to a bare [ack] ("[ack].") is still a tone, not speech.
_ACK_FILLER = " \t\r\n.!?,;:。！？；，、…"


def _ack_body(rest: str) -> str:
    return rest.lstrip(_ACK_FILLER)


class IgnoreGate:
    def __init__(self, markers: dict = None):
        self._markers = markers if markers is not None else DEFAULT_MARKERS
        self._held = ""
        self._state = None  # None = undecided, "pass" = speak, else the matched marker's key

    @property
    def ignoring(self) -> bool:
        return self._state == "ignore"

    @property
    def matched(self):
        """The matched marker's key ("ignore", "ack", ...), or None if passed through / still undecided."""
        return self._state if self._state != "pass" else None

    def feed(self, chunk: str) -> str:
        """Text that may be spoken now (empty while undecided or when the reply is a marker)."""
        if self._state == "pass":
            return chunk
        if self._state is not None:
            return ""
        self._held += chunk
        head = self._held.lstrip()
        if not head:
            return ""
        ignore = self._markers.get("ignore")
        if ignore and head.startswith(ignore):
            self._state = "ignore"  # the whole reply, including anything after the marker
            self._held = ""
            return ""
        ack = self._markers.get("ack")
        if ack and head.startswith(ack):
            body = _ack_body(head[len(ack):])
            if body == "":
                return ""  # still a bare ack, or the next chunk may add words
            self._state = "pass"
            self._held = ""
            return body
        if any(marker.startswith(head) for marker in self._markers.values()):
            return ""  # could still become one of the markers
        self._state = "pass"
        out, self._held = self._held, ""
        return out

    def flush(self) -> str:
        """End of reply: a held bare [ack] becomes the tone; anything else still held is spoken."""
        if self._state not in (None, "pass"):
            self._held = ""
            return ""
        if self._state == "pass":
            out, self._held = self._held, ""
            return out
        head = self._held.lstrip()
        ack = self._markers.get("ack")
        if ack and head.startswith(ack) and _ack_body(head[len(ack):]) == "":
            self._state = "ack"
            self._held = ""
            return ""
        ignore = self._markers.get("ignore")
        if ignore and head.startswith(ignore):
            self._state = "ignore"
            self._held = ""
            return ""
        out, self._held = self._held, ""
        return out
