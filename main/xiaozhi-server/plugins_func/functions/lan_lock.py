"""Voice / typed tool: lock the voice server to this Mac (no network devices), or open it again."""
from core import lan_guard
from core.utils.audio_device import is_board
from plugins_func.register import Action, ActionResponse, ToolType, register_function

lan_lock_desc = {
    "type": "function",
    "function": {
        "name": "lan_lock",
        "description": (
            "Control who can reach Vibi over the network. Use 'lock' for 'lock Vibi to the Mac', 'lock it down', "
            "'Mac only'; 'unlock' for 'unlock Vibi', 'let the board connect again'; 'status' for 'is Vibi locked'."
        ),
        "parameters": {
            "type": "object",
            "properties": {"action": {"type": "string", "enum": ["lock", "unlock", "status"]}},
            "required": ["action"],
        },
    },
}


@register_function("lan_lock", lan_lock_desc, ToolType.SYSTEM_CTL)
def lan_lock(conn, action="status"):
    if action == "lock":
        lan_guard.set_locked(True)
        say = "Locked to this Mac. Devices on the network are disconnected. Say unlock Vibi on the Mac to undo it."
    elif action == "unlock":
        # Only the Mac app may open it again; a board (also over Bluetooth) may lock but not unlock.
        if is_board(conn):
            say = "Unlocking only works on the Mac. Say or type unlock Vibi there."
        else:
            lan_guard.set_locked(False)
            say = "Unlocked. Trusted devices can connect again."
    else:
        say = "Vibi is locked to this Mac." if lan_guard.locked() else "Vibi accepts trusted devices on the network."
    return ActionResponse(Action.RESPONSE, say, say)
