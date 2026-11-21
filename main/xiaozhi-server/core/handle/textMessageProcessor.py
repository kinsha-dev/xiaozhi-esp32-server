import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
from core.handle.textMessageHandlerRegistry import TextMessageHandlerRegistry

TAG = __name__


class TextMessageProcessor:
    """Main message processor class"""

    def __init__(self, registry: TextMessageHandlerRegistry):
        self.registry = registry

    async def process_message(self, conn: "ConnectionHandler", message: str) -> None:
        """Main entry for handling a message"""
        try:
            # parse the JSON message
            msg_json = json.loads(message)

            # handle the JSON message
            if isinstance(msg_json, dict):
                message_type = msg_json.get("type")

                # log it
                conn.logger.bind(tag=TAG).info(f"Received {message_type} message: {message}")

                # get and run the handler
                handler = self.registry.get_handler(message_type)
                if handler:
                    await handler.handle(conn, msg_json)
                else:
                    conn.logger.bind(tag=TAG).error(f"Received a message of unknown type: {message}")
            # handle a pure-digit message
            elif isinstance(msg_json, int):
                conn.logger.bind(tag=TAG).info(f"Received a numeric message: {message}")
                await conn.websocket.send(message)

        except json.JSONDecodeError:
            # forward non-JSON messages directly
            conn.logger.bind(tag=TAG).error(f"Parsed an invalid message: {message}")
            await conn.websocket.send(message)
