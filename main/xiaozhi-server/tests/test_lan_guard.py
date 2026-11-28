import asyncio

import pytest

from core.lan_guard import allowed, enforce, locked, set_locked
from plugins_func.functions.lan_lock import lan_lock

BOARD = {"device-id": "aa:bb:cc:dd:ee:ff", "client-id": "1234-uuid"}
TRUSTED = [{"device_id": "AA:BB:CC:DD:EE:FF", "client_id": "1234-uuid"}]


@pytest.mark.parametrize("ip", ["127.0.0.1", "::1", "::ffff:127.0.0.1"])
def test_this_mac_is_allowed(ip):
    assert allowed(ip, {"device-id": "mac-app", "client-id": "mac-app"}, [])


def test_web_page_on_this_mac_is_refused():
    assert not allowed("127.0.0.1", {"device-id": "x", "origin": "https://example.com"}, TRUSTED)


def test_trusted_board_is_allowed():
    assert allowed("192.168.1.20", BOARD, TRUSTED)


@pytest.mark.parametrize("headers", [
    {},
    {"device-id": BOARD["device-id"]},
    {**BOARD, "client-id": "guess"},
    {**BOARD, "device-id": "11:22:33:44:55:66"},
    {"device-id": "mac-app", "client-id": "mac-app"},
])
def test_other_network_clients_are_refused(headers):
    assert not allowed("192.168.1.66", headers, TRUSTED + [{"device_id": "mac-app", "client_id": "mac-app"}])


@pytest.mark.parametrize("trusted", [None, [], ["aa:bb:cc:dd:ee:ff"], [{"device_id": "aa:bb:cc:dd:ee:ff"}]])
def test_nothing_trusted_means_no_network_access(trusted):
    assert not allowed("192.168.1.20", BOARD, trusted)


@pytest.mark.parametrize("ip", [None, "", "not-an-ip"])
def test_unknown_peer_is_treated_as_remote(ip):
    assert not allowed(ip, {"device-id": "x"}, [])


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_BOX_DATA", str(tmp_path))
    return tmp_path


class Conn:
    def __init__(self, device):
        self.headers = {"device-id": device}


def test_lock_refuses_the_network_but_not_this_mac():
    set_locked(True)
    assert not allowed("192.168.1.20", BOARD, TRUSTED)
    assert allowed("127.0.0.1", {"device-id": "mac-app"}, [])
    set_locked(False)
    assert allowed("192.168.1.20", BOARD, TRUSTED)


def test_board_can_lock_but_only_the_mac_can_unlock():
    lan_lock(Conn("aa:bb"), "lock")
    assert locked()
    lan_lock(Conn("aa:bb"), "unlock")
    assert locked()
    lan_lock(Conn("mac-app"), "unlock")
    assert not locked()


@pytest.mark.parametrize("ip,closed", [("192.168.1.20", True), ("127.0.0.1", False)])
def test_lock_closes_open_network_connections(ip, closed):
    class Socket:
        closed = False

        async def close(self):
            self.closed = True

    set_locked(True)
    sock = Socket()
    asyncio.run(enforce(sock, ip))
    assert sock.closed is closed
