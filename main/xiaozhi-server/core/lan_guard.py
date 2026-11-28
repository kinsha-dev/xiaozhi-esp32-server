"""Who may open the voice websocket. The server listens on the network so the board can reach it.

- This Mac (loopback: the Mac app, the Bluetooth relay) is allowed, unless the request carries an Origin header:
  only browsers send one, and a web page must not be able to drive Vibi.
- Anything else must match an entry of server.auth.trusted_devices by BOTH device-id and client-id. The client-id is
  the random UUID the board stores, so knowing its MAC address is not enough. An empty list lets no remote device in.
  The device-id "mac-app" is never accepted from the network (it skips Mute and Stealth).
- Lock to this Mac (data/lan_lock exists, set by the lan_lock tool): every network device is refused, trusted or not,
  and open network connections are closed. The Bluetooth relay runs on this Mac, so it still works.

This is not encryption: ws:// traffic can still be read by someone able to capture it on the same network.
"""
import asyncio
import hmac
import ipaddress

from core.utils.audio_device import MAC_APP_ID, _dir

POLL_S = 2.0


def locked() -> bool:
    return (_dir() / "lan_lock").exists()


def set_locked(on: bool):
    f = _dir() / "lan_lock"
    if on:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("1")
    else:
        f.unlink(missing_ok=True)


async def enforce(websocket, peer_ip):
    """Per-connection task: close a network connection once the lock goes on. Loopback is left alone."""
    if is_loopback(peer_ip):
        return
    while not locked():
        await asyncio.sleep(POLL_S)
    await websocket.close()


def is_loopback(ip) -> bool:
    try:
        addr = ipaddress.ip_address(str(ip).split("%")[0])
    except ValueError:
        return False
    return (getattr(addr, "ipv4_mapped", None) or addr).is_loopback


def allowed(peer_ip, headers: dict, trusted) -> bool:
    """peer_ip is the socket's address (never a forwarded-for header); headers has lower-case names."""
    if is_loopback(peer_ip):
        return "origin" not in headers
    if locked():
        return False
    device = (headers.get("device-id") or "").strip().lower()
    client = (headers.get("client-id") or "").strip()
    if not device or not client or device == MAC_APP_ID:
        return False
    for entry in trusted or []:
        if not isinstance(entry, dict):
            continue
        want_device = str(entry.get("device_id") or "").strip().lower()
        want_client = str(entry.get("client_id") or "").strip()
        if want_client and want_device == device and hmac.compare_digest(client.encode(), want_client.encode()):
            return True
    return False
