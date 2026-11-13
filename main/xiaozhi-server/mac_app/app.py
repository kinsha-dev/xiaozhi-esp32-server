"""Menu-bar app: `python -m mac_app`."""
import json
import os
import subprocess
import sys
import threading
import time
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
    from mac_app.ctrl_tunnel import Tunnel

    panel = CtrlPanel(ctl.cfg.data_dir, SERVER_DIR, lambda text: ctl.start_session(text, force=True),
                      quit_app=lambda: rumps.quit_application(), set_muted=lambda on: ctl.set_muted(on))
    tunnel = Tunnel(panel, ctl.cfg.data_dir)
    overlay = Overlay(lambda: 0.3 if ctl.mirroring else ctl._speaker.level(), lambda: 0.05 if ctl.mirroring else ctl.mic_level)

    class MenuApp(rumps.App):
        def __init__(self):
            super().__init__("vibi", title=ICONS["idle"], quit_button=None)
            self.status = rumps.MenuItem("Say “Hi bibi”")
            self.mute = rumps.MenuItem("Mute (board and Mac)", callback=self.on_mute)
            self.mute.state = ctl.muted
            self.retry = rumps.MenuItem("Retry microphone", callback=lambda _: ctl.retry_mic())
            self.login = rumps.MenuItem("Start at Login", callback=self.on_login)
            self.login.state = login_item_enabled()
            self.devices = {m: rumps.MenuItem(t, callback=self.on_device) for m, t in
                            (("auto", "Auto (board when connected)"), ("mac", "This Mac"), ("board", "Board"))}
            self.device_menu = rumps.MenuItem("Audio device")
            for it in self.devices.values():
                self.device_menu.add(it)
            self._show_device(ctl.device_mode())
            self.links = {m: rumps.MenuItem(t, callback=self.on_link) for m, t in
                          (("auto", "Auto (Wi-Fi, Bluetooth if needed)"), ("bluetooth", "Bluetooth"))}
            self.link_menu = rumps.MenuItem("Board link")
            for it in self.links.values():
                self.link_menu.add(it)
            self._show_link(ctl.board_link())
            self.auto = rumps.MenuItem("Speak updates on their own", callback=self.on_auto)
            self.auto.state = ctl.auto_speak()
            self.ctrl = rumps.MenuItem("Vibi Ctrl…  (⌃I or ⌃⌥I)", callback=lambda _: panel.open())
            self.remote = rumps.MenuItem("Remote access (tunnel)", callback=self.on_remote)
            self.menu = [self.status, self.ctrl, self.mute, self.device_menu, self.link_menu, self.auto, self.retry, self.login, None, rumps.MenuItem("Quit", callback=self.on_quit)]
            self.timer = rumps.Timer(self.refresh, 0.05)
            self.timer.start()

        def refresh(self, _):
            s = ctl.display_state()
            self.title = ICONS.get(s, ICONS["idle"])
            tunnel.poll_cmd()
            self.remote.state = tunnel.running()
            self.mute.state = ctl.muted  # follows a mute set from Vibi Ctrl or the CLI
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

        def _show_link(self, mode):
            for m, it in self.links.items():
                it.state = m == mode

        def on_link(self, item):
            mode = next(m for m, it in self.links.items() if it is item)
            ctl.set_board_link(mode)
            self._show_link(mode)

        def on_auto(self, item):
            item.state = not item.state
            ctl.set_auto_speak(bool(item.state))

        def on_mute(self, item):
            item.state = not item.state
            ctl.set_muted(bool(item.state))

        def on_remote(self, item):
            if tunnel.running():
                tunnel.stop()
                return
            if not tunnel.start():
                rumps.alert("Remote access", tunnel.error)
                return
            threading.Thread(target=self._announce_remote, daemon=True).start()

        def _announce_remote(self):
            for _ in range(60):
                if tunnel.url:
                    subprocess.run(["pbcopy"], input=tunnel.link().encode())  # login link goes to the clipboard, not the log
                    print(f"[ctrl] remote access on: {tunnel.url} (login link copied to clipboard)", flush=True)
                    return
                time.sleep(0.5)

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
    try:  # the log can be hard to find: leave the result where the panel/support can read it
        (ctl.cfg.data_dir / "ctrl_hotkeys.json").write_text(json.dumps({"pid": os.getpid(), "armed": armed}))
    except OSError:
        pass
    MenuApp().run()
