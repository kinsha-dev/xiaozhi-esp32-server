"""Is a Bluetooth headset the Mac's current microphone, and is anything else playing audio on the Mac?"""
import json
import re
import subprocess


def bluetooth_input() -> bool:
    """True when the default input device is a Bluetooth one (macOS reports its transport)."""
    try:
        out = subprocess.run(["system_profiler", "SPAudioDataType", "-json"], capture_output=True, text=True, timeout=8).stdout
        groups = json.loads(out).get("SPAudioDataType", [])
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    for group in groups:
        for item in group.get("_items", []):
            if item.get("coreaudio_default_audio_input_device") == "spaudio_yes":
                return "bluetooth" in str(item.get("coreaudio_device_transport", "")).lower()
    return False


def other_audio_playing(own_pids) -> bool:
    """True while a process other than `own_pids` holds the speakers open (browser tab, video, music)."""
    try:
        out = subprocess.run(["pmset", "-g", "assertions"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    pid = None
    for line in out.splitlines():
        m = re.search(r"Created for PID: (\d+)", line)
        if m:
            pid = int(m.group(1))
        elif "Resources:" in line and "audio-out" in line and pid is not None and pid not in own_pids:
            return True
    return False
