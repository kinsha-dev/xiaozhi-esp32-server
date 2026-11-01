"""Messages of the xiaozhi websocket protocol, as the ESP32 board speaks it."""
import json
from dataclasses import dataclass
from typing import Literal, Optional

UP_AUDIO = {"format": "opus", "sample_rate": 16000, "channels": 1, "frame_duration": 60}


@dataclass
class ServerEvent:
    kind: str  # "hello" | "tts" | "stt" | "llm" | "other"
    state: Optional[str] = None
    text: Optional[str] = None
    session_id: Optional[str] = None
    audio_params: Optional[dict] = None


def hello_message() -> str:
    return json.dumps({
        "type": "hello",
        "version": 1,
        "transport": "websocket",
        "features": {"mcp": False},
        "audio_params": UP_AUDIO,
    })


def listen_message(session_id: str, state: Literal["start", "stop"]) -> str:
    return json.dumps({"session_id": session_id, "type": "listen", "state": state, "mode": "auto"})


def detect_message(session_id: str, text: str) -> str:
    return json.dumps({"session_id": session_id, "type": "listen", "state": "detect", "text": text})


def abort_message(session_id: str) -> str:
    return json.dumps({"session_id": session_id, "type": "abort"})


def parse_server_message(raw: str) -> ServerEvent:
    try:
        msg = json.loads(raw)
    except (TypeError, ValueError):
        return ServerEvent("other")
    if not isinstance(msg, dict):
        return ServerEvent("other")
    kind = msg.get("type")
    if kind not in ("hello", "tts", "stt", "llm"):
        return ServerEvent("other")
    return ServerEvent(kind, msg.get("state"), msg.get("text"), msg.get("session_id"), msg.get("audio_params"))
