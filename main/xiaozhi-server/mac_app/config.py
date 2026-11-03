import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

SERVER_DIR = Path(__file__).resolve().parents[1]
# the outbox plugin resolves its symlink into voice-note-box, so that is where outbox.jsonl really lives
DEFAULT_DATA = Path.home() / "esp/voice-note-box/data"


@dataclass
class Config:
    server_url: str
    wake_phrase: str
    data_dir: Path
    speaker_rate: int
    idle_timeout: float = 300.0


def load(env: Optional[Mapping[str, str]] = None) -> Config:
    env = os.environ if env is None else env
    return Config(
        server_url=env.get("MAC_APP_SERVER", "ws://127.0.0.1:8000/xiaozhi/v1/"),
        wake_phrase=env.get("MAC_APP_WAKE", "hi bibi"),
        data_dir=Path(env.get("VOICE_BOX_DATA", DEFAULT_DATA if DEFAULT_DATA.exists() else SERVER_DIR / "data")),
        speaker_rate=int(env.get("MAC_APP_SPEAKER_RATE", "24000")),
        idle_timeout=float(env.get("MAC_APP_IDLE_SECONDS", "300")),
    )
