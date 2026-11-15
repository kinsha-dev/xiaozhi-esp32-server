"""Drop a reply that is only a silent-style marker, while letting every other reply stream through untouched.

The prompt tells the model to answer `[ignore]` when the heard speech is background chatter not addressed to
Vibi, and `[ack]` when the whole reply is nothing but a bare acknowledgment ("okay", "got it", "sure") that
isn't worth speaking out loud - a tone covers both better than a full sentence or dead silence (see
core/utils/tones.py: "confused" for [ignore], "confirm" for [ack]). A marker may arrive split across stream
chunks, so the start of a reply is held back until it is clear whether it is one of the markers or ordinary
text.
"""

IGNORE_MARKER = "[ignore]"
ACK_MARKER = "[ack]"
DEFAULT_MARKERS = {"ignore": IGNORE_MARKER, "ack": ACK_MARKER}


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
        for key, marker in self._markers.items():
            if head.startswith(marker):
                self._state = key
                return ""
        if any(marker.startswith(head) for marker in self._markers.values()):
            return ""  # could still become one of the markers
        self._state = "pass"
        out, self._held = self._held, ""
        return out

    def flush(self) -> str:
        """End of reply: anything still held was not a marker, so release it."""
        out = "" if self._state not in (None, "pass") else self._held
        self._held = ""
        return out
