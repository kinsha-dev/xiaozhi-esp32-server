"""Start/stop a Cloudflare quick tunnel to Vibi Ctrl's token-protected remote listener. Off by default, never at login.
The app polls data/ctrl_tunnel.cmd ("start"/"stop", written by bin/vibi-tunnel) and publishes data/ctrl_tunnel.json."""
import json
import re
import shutil
import subprocess
import threading
from pathlib import Path

URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


class Tunnel:
    def __init__(self, panel, data_dir):
        self.panel, self.data_dir = panel, Path(data_dir)
        self.proc, self.url, self.error = None, None, None
        self._lock = threading.Lock()

    def binary(self):
        return shutil.which("cloudflared") or next((p for p in ("/opt/homebrew/bin/cloudflared", "/usr/local/bin/cloudflared") if Path(p).exists()), None)

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def link(self):
        """Link to copy: with the one-time login token while token login is on for this host (shown to the user only,
        never logged); the plain URL once a passkey replaces it."""
        if not self.url:
            return None
        host = self.url.split("//", 1)[-1]
        return f"{self.url}/?t={self.panel.remote_token()}" if self.panel.passkeys.token_login_enabled(host) else self.url

    def named(self):
        """(tunnel name, hostname) of a configured named tunnel, else None."""
        try:
            d = json.loads((self.data_dir / "ctrl_tunnel_named.json").read_text())
            return (str(d["name"]), str(d["hostname"]).lower())
        except (OSError, ValueError, KeyError):
            return None

    def start(self):
        with self._lock:
            if self.running():
                return True
            exe = self.binary()
            if not exe:
                self.error = "cloudflared is not installed (brew install cloudflared)"
                self._publish()
                return False
            self.error, self.url = None, None
            port = self.panel.start_remote()
            named = self.named()  # stable hostname: passkeys survive restarts
            cmd = ([exe, "tunnel", "--no-autoupdate", "run", "--url", f"http://127.0.0.1:{port}", named[0]] if named
                   else [exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"])
            self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, start_new_session=True)
            if named:
                self.url = f"https://{named[1]}"
            threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
            self._publish()
            return True

    def _read(self, proc):
        for line in proc.stderr:  # cloudflared logs its URL here; nothing else from it is kept
            m = URL_RE.search(line)
            if m and not self.url:
                self.url = m.group(0)
                self._publish()
        self._publish()

    def stop(self):
        with self._lock:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            self.proc, self.url = None, None
            self.panel.stop_remote()
            self._publish()

    def _publish(self):
        """Public state only: the token is never written here."""
        try:
            (self.data_dir / "ctrl_tunnel.json").write_text(json.dumps({"running": self.running(), "url": self.url, "error": self.error}))
        except OSError:
            pass

    def poll_cmd(self):
        f = self.data_dir / "ctrl_tunnel.cmd"
        try:
            cmd = f.read_text().strip()
            f.unlink()
        except OSError:
            return
        if cmd == "start":
            self.start()
        elif cmd == "stop":
            self.stop()
