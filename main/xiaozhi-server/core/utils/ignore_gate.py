"""Drop a reply that is only the silent marker, while letting every other reply stream through untouched.

The prompt tells the model to answer `[ignore]` when the heard speech is background chatter not addressed to Vibi.
The marker may arrive split across stream chunks, so the start of a reply is held back until it is clear whether it is
the marker or ordinary text.
"""

IGNORE_MARKER = "[ignore]"


class IgnoreGate:
    def __init__(self):
        self._held = ""
        self._state = None  # None = undecided, "pass" = speak, "ignore" = stay silent

    @property
    def ignoring(self) -> bool:
        return self._state == "ignore"

    def feed(self, chunk: str) -> str:
        """Text that may be spoken now (empty while undecided or when the reply is the marker)."""
        if self._state == "pass":
            return chunk
        if self._state == "ignore":
            return ""
        self._held += chunk
        head = self._held.lstrip()
        if not head:
            return ""
        if head.startswith(IGNORE_MARKER):
            self._state = "ignore"
            return ""
        if IGNORE_MARKER.startswith(head):
            return ""  # could still become the marker
        self._state = "pass"
        out, self._held = self._held, ""
        return out

    def flush(self) -> str:
        """End of reply: anything still held was not the marker, so release it."""
        out = "" if self._state == "ignore" else self._held
        self._held = ""
        return out
