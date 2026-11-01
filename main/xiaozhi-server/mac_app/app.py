"""Menu-bar app: `python -m mac_app`."""
import os
import subprocess
import sys
from pathlib import Path

from mac_app.config import SERVER_DIR, load
from mac_app.controller import Controller

__all__ = ["Controller", "main"]

ICONS = {"idle": "vb", "connecting": "vb…", "listening": "vb●", "thinking": "vb…", "speaking": "vb♪", "muted": "vb⊘", "error": "vb!"}
LABEL = "com.voicebox.macapp"
PLIST = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
TEMPLATE = Path(__file__).with_name("com.voicebox.macapp.plist.template")


def notify(text: str):
    body = text.replace("\\", " ").replace('"', "'")[:180]
    subprocess.run(["osascript", "-e", f'display notification "{body}" with title "vibi"'], check=False, timeout=10)


def login_item_enabled() -> bool:
    return PLIST.exists()


def set_login_item(enabled: bool):
    domain = f"gui/{os.getuid()}"
    if enabled:
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        PLIST.write_text(TEMPLATE.read_text().replace("{python}", sys.executable).replace("{workdir}", str(SERVER_DIR)))
        subprocess.run(["launchctl", "bootstrap", domain, str(PLIST)], check=False)
    else:
        subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], check=False)
        PLIST.unlink(missing_ok=True)


def build_controller():
    from mac_app.audio import Mic, Speaker
    from mac_app.session import Session
    from mac_app.wake import WakeDetector, whisper_transcriber

    cfg = load()
    speaker = Speaker(cfg.speaker_rate)
    transcribe = whisper_transcriber()
    if os.environ.get("MAC_APP_DEBUG"):
        def transcribe(audio, _t=transcribe):  # noqa: F811
            text = _t(audio)
            print(f"[wake] heard: {text!r} peak={abs(audio).max():.3f}", flush=True)
            return text
    detector = WakeDetector(transcribe, cfg.wake_phrase)
    return Controller(
        cfg, speaker, detector, lambda cb: Mic(cb),
        lambda q, on_state: Session(cfg.server_url, q, speaker, idle_timeout=cfg.idle_timeout, on_state=lambda st: (print(f"[session] {st}", flush=True), on_state(st))[1]
                                    if os.environ.get("MAC_APP_DEBUG") else on_state(st)), notify)


def main():
    import rumps

    ctl = build_controller()

    class MenuApp(rumps.App):
        def __init__(self):
            super().__init__("vibi", title=ICONS["idle"], quit_button=None)
            self.status = rumps.MenuItem("Say “Hi bibi”")
            self.mute = rumps.MenuItem("Mute", callback=self.on_mute)
            self.retry = rumps.MenuItem("Retry microphone", callback=lambda _: ctl.retry_mic())
            self.login = rumps.MenuItem("Start at Login", callback=self.on_login)
            self.login.state = login_item_enabled()
            self.menu = [self.status, self.mute, self.retry, self.login, None, rumps.MenuItem("Quit", callback=self.on_quit)]
            self.timer = rumps.Timer(self.refresh, 0.3)
            self.timer.start()

        def refresh(self, _):
            s = ctl.display_state()
            self.title = ICONS.get(s, ICONS["idle"])
            self.status.title = ctl.error if ctl.error else (f"State: {s}" if s != "idle" else "Say “Hi bibi”")

        def on_mute(self, item):
            item.state = not item.state
            ctl.set_muted(bool(item.state))

        def on_login(self, item):
            item.state = not item.state
            set_login_item(bool(item.state))

        def on_quit(self, _):
            ctl.stop()
            rumps.quit_application()

    ctl.start()
    MenuApp().run()
