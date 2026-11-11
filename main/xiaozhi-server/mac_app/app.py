"""Menu-bar app: `python -m mac_app`."""
import json
import os
import subprocess
import sys
from pathlib import Path

from mac_app.ble_central import BleService, ensure_bluetooth_usage
from mac_app.config import SERVER_DIR, load
from mac_app.controller import Controller

__all__ = ["Controller", "main"]

ICONS = {"idle": "vb", "connecting": "vb…", "listening": "vb●", "thinking": "vb…", "speaking": "vb♪", "muted": "vb⊘", "error": "vb!", "offline": "X"}
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
    from mac_app.audio import Mic, SaySpeaker, Speaker
    from mac_app.session import Session
    from mac_app.wake import WakeDetector, whisper_transcriber

    cfg = load()
    speaker = Speaker(cfg.speaker_rate) if os.environ.get("MAC_APP_VOICE") == "edge" else SaySpeaker(os.environ.get("MAC_APP_VOICE", ""))
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
    import faulthandler

    import rumps

    faulthandler.enable()  # native crashes (PortAudio, Opus) leave a traceback in data/mac_app.log

    from mac_app.overlay import Overlay

    ctl = build_controller()
    ensure_bluetooth_usage()
    ble = BleService(ctl.cfg)
    ble.start()
    from mac_app.ctrl import CtrlPanel, register_hotkeys

    panel = CtrlPanel(ctl.cfg.data_dir, SERVER_DIR, lambda text: ctl.start_session(text, force=True),
                      quit_app=lambda: rumps.quit_application())
    overlay = Overlay(lambda: 0.3 if ctl.mirroring else ctl._speaker.level(), lambda: 0.05 if ctl.mirroring else ctl.mic_level)

    class MenuApp(rumps.App):
        def __init__(self):
            super().__init__("vibi", title=ICONS["idle"], quit_button=None)
            self.status = rumps.MenuItem("Say “Hi bibi”")
            self.mute = rumps.MenuItem("Mute", callback=self.on_mute)
            self.retry = rumps.MenuItem("Retry microphone", callback=lambda _: ctl.retry_mic())
            self.login = rumps.MenuItem("Start at Login", callback=self.on_login)
            self.login.state = login_item_enabled()
            self.devices = {m: rumps.MenuItem(t, callback=self.on_device) for m, t in
                            (("auto", "Auto (board when connected)"), ("mac", "This Mac"), ("board", "Board"))}
            self.device_menu = rumps.MenuItem("Audio device")
            for it in self.devices.values():
                self.device_menu.add(it)
            self._show_device(ctl.device_mode())
            self.ctrl = rumps.MenuItem("Vibi Ctrl…  (⌃I or ⌃⌥I)", callback=lambda _: panel.open())
            self.menu = [self.status, self.ctrl, self.mute, self.device_menu, self.retry, self.login, None, rumps.MenuItem("Quit", callback=self.on_quit)]
            self.timer = rumps.Timer(self.refresh, 0.05)
            self.timer.start()

        def refresh(self, _):
            s = ctl.display_state()
            self.title = ICONS.get(s, ICONS["idle"])
            try:
                overlay.update(s)
            except Exception as e:  # never let the overlay break the menu bar
                print(f"[overlay] {e}", flush=True)
            self.status.title = ctl.error if ctl.error else ("Board offline: no heartbeat for 5 min" if s == "offline" else f"State: {s}" if s != "idle" else "Say “Hi bibi”")

        def _show_device(self, mode):
            for m, it in self.devices.items():
                it.state = m == mode

        def on_device(self, item):
            mode = next(m for m, it in self.devices.items() if it is item)
            ctl.set_device_mode(mode)
            self._show_device(mode)

        def on_mute(self, item):
            item.state = not item.state
            ctl.set_muted(bool(item.state))

        def on_login(self, item):
            item.state = not item.state
            set_login_item(bool(item.state))

        def on_quit(self, _):
            ble.stop()
            ctl.stop()
            rumps.quit_application()

    ctl.start()
    armed = register_hotkeys(panel.open)
    print(f"[ctrl] hotkeys: {armed}", flush=True)
    try:  # leave the result where support can read it
        (ctl.cfg.data_dir / "ctrl_hotkeys.json").write_text(json.dumps({"pid": os.getpid(), "armed": armed}))
    except OSError:
        pass
    MenuApp().run()
