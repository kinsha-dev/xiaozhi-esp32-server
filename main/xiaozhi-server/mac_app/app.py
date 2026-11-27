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


def cu_running(max_age=600):
    """A computer-use run is active: its state file (data/computer_use/state.json) exists and is recent."""
    f = Path(os.environ.get("VOICE_BOX_DATA", Path.home() / "esp/voice-note-box/data")) / "computer_use" / "state.json"
    try:
        return time.time() - f.stat().st_mtime < max_age
    except OSError:
        return False

from mac_app.controller import Controller

__all__ = ["Controller", "main"]

ICONS = {"idle": "vb", "connecting": "vb…", "listening": "vb●", "thinking": "vb…", "speaking": "vb♪", "muted": "vb⊘", "stealth": "vb◌", "error": "vb!", "offline": "X"}
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


def install_dock_menu(ctl, menu_app):
    """Right-click the Dock icon: "Disable tones" with a checkmark, kept in step with the menu-bar item."""
    import objc
    from AppKit import NSMenu, NSMenuItem
    from rumps import rumps as _r

    delegate = _r.NSApp

    class _Toggle(objc.lookUpClass("NSObject")):
        def toggle_(self, sender):
            on = not ctl.tones_disabled()
            ctl.set_tones_disabled(on)
            menu_app.tones.state = on

    toggler = _Toggle.alloc().init()

    def applicationDockMenu_(self, app):
        menu = NSMenu.alloc().init()
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Disable tones", "toggle:", "")
        item.setTarget_(toggler)
        item.setState_(1 if ctl.tones_disabled() else 0)
        menu.addItem_(item)
        return menu

    objc.classAddMethods(delegate, [objc.selector(applicationDockMenu_, signature=b"@@:@")])
    install_dock_menu._keep = toggler


def main():
    import faulthandler

    import rumps

    faulthandler.enable()  # native crashes (PortAudio, Opus) leave a traceback in data/mac_app.log

    from mac_app.overlay import Overlay

    ctl = build_controller()
    ensure_bluetooth_usage()
    ble = BleService(ctl.cfg)
    ble.start()
    from mac_app.collector import BoardService, CollectorService
    collector = CollectorService()
    collector.start()
    board = BoardService()
    board.start()
    from mac_app.ctrl import CtrlPanel, register_hotkeys
    from mac_app.ctrl_tunnel import Tunnel

    panel = CtrlPanel(ctl.cfg.data_dir, SERVER_DIR, lambda text: ctl.start_session(text, force=True),
                      quit_app=lambda: rumps.quit_application(), set_muted=lambda on: ctl.set_muted(on), set_stealth=lambda on: ctl.set_stealth(on))
    tunnel = Tunnel(panel, ctl.cfg.data_dir)
    overlay = Overlay(lambda: 0.3 if ctl.mirroring else ctl._speaker.level(), lambda: 0.05 if ctl.mirroring else ctl.mic_level)

    class MenuApp(rumps.App):
        def __init__(self):
            super().__init__("vibi", title=ICONS["idle"], quit_button=None)
            self.status = rumps.MenuItem("Say “Hi bibi”")
            self.modes = {m: rumps.MenuItem(t, callback=self.on_mode) for m, t in
                          (("active", "Active (listen and speak)"), ("stealth", "Stealth (animation and typed chat only)"),
                           ("mute", "Mute (board and Mac)"))}
            self.mode_menu = rumps.MenuItem("Listen mode")
            for it in self.modes.values():
                self.mode_menu.add(it)
            self._show_mode(ctl.listen_mode())
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
            self.tones = rumps.MenuItem("Disable tones", callback=self.on_tones)
            self.tones.state = ctl.tones_disabled()
            self.animation = rumps.MenuItem("Voice animation", callback=self.on_animation)
            self.animation.state = ctl.voice_animation_enabled()
            self.ctrl = rumps.MenuItem("Vibi Ctrl")  # same tabs as the Vibi Ctrl panel; each opens it on that tab
            self.ctrl.add(rumps.MenuItem("Open  (⌃I or ⌃⌥I)", callback=lambda _: panel.open()))
            self.ctrl.add(None)
            for key, name in (("chat", "Chat"), ("notes", "Notes"), ("components", "Components")):
                self.ctrl.add(rumps.MenuItem(name, callback=lambda _, k=key: panel.open(k)))
            self.remote = rumps.MenuItem("Remote tunnel: off (click to start)", callback=self.on_remote)
            self.remote_copy = rumps.MenuItem("Copy remote link", callback=self.on_copy_remote)
            self.menu = [self.status, self.ctrl, self.remote, self.remote_copy, self.mode_menu, self.device_menu, self.link_menu, self.auto, self.animation, self.tones, self.retry, self.login, None, rumps.MenuItem("Quit", callback=self.on_quit)]
            self.timer = rumps.Timer(self.refresh, 0.05)
            self.timer.start()

        def refresh(self, _):
            s = ctl.display_state()
            self.title = ICONS.get(s, ICONS["idle"])
            tunnel.poll_cmd()
            self._show_remote()
            self._show_mode(ctl.listen_mode())  # follows a mode set from Vibi Ctrl or the CLI
            try:
                busy = s == "idle" and cu_running()  # a computer-use run is working in the background
                overlay.update(("working" if busy else s) if ctl.voice_animation_enabled() else "idle")  # "idle": hidden when disabled
            except Exception as e:  # never let the overlay break the menu bar
                print(f"[overlay] {e}", flush=True)
            self.status.title = ctl.error if ctl.error else ("Board offline: no heartbeat for 5 min" if s == "offline" else "Stealth: type commands in Vibi Ctrl" if s == "stealth" else f"State: {s}" if s != "idle" else "Say “Hi bibi”")

        def _show_remote(self):
            on = tunnel.running()
            title = ("Remote tunnel: starting…" if not tunnel.url else f"Remote tunnel: on, {tunnel.url.split('//', 1)[-1]} (click to stop)") if on else "Remote tunnel: off (click to start)"
            if self.remote.title != title:
                self.remote.title = title
            self.remote.state = on
            if (self.remote_copy.callback is not None) != (on and bool(tunnel.url)):  # enabled only while a URL exists
                self.remote_copy.set_callback(self.on_copy_remote if on and tunnel.url else None)

        def on_copy_remote(self, _):
            link = tunnel.link()
            if link:
                subprocess.run(["pbcopy"], input=link.encode())  # may hold the login token: clipboard only, never logged

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

        def on_tones(self, item):
            item.state = not item.state
            ctl.set_tones_disabled(bool(item.state))

        def on_animation(self, item):
            item.state = not item.state
            ctl.set_voice_animation(bool(item.state))

        def _show_mode(self, mode):
            for m, it in self.modes.items():
                it.state = m == mode

        def on_mode(self, item):
            mode = next(m for m, it in self.modes.items() if it is item)
            ctl.set_listen_mode(mode)
            self._show_mode(mode)

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
            collector.stop()
            board.stop()
            ctl.stop()
            rumps.quit_application()

    ctl.start()
    from mac_app import cu_agent
    cu_agent.start(ctl.cfg.data_dir)
    armed = register_hotkeys(panel.open)
    print(f"[ctrl] hotkeys: {armed}", flush=True)
    try:  # the log can be hard to find: leave the result where the panel/support can read it
        (ctl.cfg.data_dir / "ctrl_hotkeys.json").write_text(json.dumps({"pid": os.getpid(), "armed": armed}))
    except OSError:
        pass
    app = MenuApp()
    install_dock_menu(ctl, app)
    app.run()
