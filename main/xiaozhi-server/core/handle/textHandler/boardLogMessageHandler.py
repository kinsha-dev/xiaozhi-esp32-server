from typing import Dict, Any

from core.handle.textMessageHandler import TextMessageHandler
from core.handle.textMessageType import TextMessageType

TAG = __name__


class BoardLogMessageHandler(TextMessageHandler):
    """Persistent disconnect diagnostics uploaded by the board after (re)connect.

    Format: {"type":"boardlog","boot":N,"reset":"panic","uptime_s":N,
             "events":["<line>", ...]}
    Each event is logged with a [board-log] tag.
    """

    @property
    def message_type(self) -> TextMessageType:
        return TextMessageType.BOARDLOG

    async def handle(self, conn, msg_json: Dict[str, Any]) -> None:
        device = getattr(conn, "device_id", None) or conn.headers.get("device-id", "?")
        log = conn.logger.bind(tag=TAG)
        log.info(
            f"[board-log] device={device} reset={msg_json.get('reset')} "
            f"boot={msg_json.get('boot')} uptime_s={msg_json.get('uptime_s')} "
            f"events={len(msg_json.get('events') or [])}"
        )
        for line in msg_json.get("events") or []:
            log.info(f"[board-log] {device} {line}")
