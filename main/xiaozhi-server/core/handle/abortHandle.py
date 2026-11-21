import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
TAG = __name__


async def handleAbortMessage(conn: "ConnectionHandler"):
    conn.logger.bind(tag=TAG).info("Abort message received")
    # set the abort state, which automatically interrupts the LLM and TTS tasks
    conn.close_after_chat = False
    conn.client_abort = True
    from core.utils.tones import stop_turn_feedback

    await stop_turn_feedback(conn)  # drop the thinking loop; do not clear client_abort
    conn.clear_queues()
    # interrupt the client speaking state
    await conn.websocket.send(
        json.dumps({"type": "tts", "state": "stop", "session_id": conn.session_id})
    )
    conn.clearSpeakStatus()
    conn.logger.bind(tag=TAG).info("Abort message received-end")
