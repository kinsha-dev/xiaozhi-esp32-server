from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
from core.handle.textMessageHandlerRegistry import TextMessageHandlerRegistry
from core.handle.textMessageProcessor import TextMessageProcessor

TAG = __name__

# global handler registry
message_registry = TextMessageHandlerRegistry()

# create the global message handler instance
message_processor = TextMessageProcessor(message_registry)


async def handleTextMessage(conn: "ConnectionHandler", message):
    """Handle a text message"""
    await message_processor.process_message(conn, message)
