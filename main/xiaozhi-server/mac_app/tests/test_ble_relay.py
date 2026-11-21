import asyncio
import time

from mac_app.ble_frag import TYPE_AUDIO, TYPE_JSON, fragment
from mac_app.ble_relay import AUDIO_FRAME_S, RelayMachine, auth_payload


class FakeWS:
    def __init__(self):
        self.sent = []
        self.closed = False
        self._incoming = asyncio.Queue()

    async def send(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True
        return self._incoming.put(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._incoming.get()
        if item is None:
            raise StopAsyncIteration
        return item


def _machine(tmp_path, frames=None, sleep=None, monotonic=None):
    sockets = []

    async def connect(headers):
        ws = FakeWS()
        ws.headers = headers
        sockets.append(ws)
        return ws

    writes = []

    async def write(data, response):
        writes.append((bytes(data), response))

    machine = RelayMachine(
        connect, tmp_path / "board_heartbeat", write, token="s3cret", mtu_payload=20,
        sleep=sleep or _asleep, monotonic=monotonic or (lambda: 0.0))
    machine._sockets = sockets
    machine._writes = writes
    return machine


async def _asleep(_dt):
    return None


def _feed(machine, seq, msg_type, payload):
    async def run():
        for frag in fragment(seq, msg_type, payload, machine.mtu_payload):
            await machine.uplink(frag)
    asyncio.run(run())


def test_auth_payload_carries_the_token():
    assert auth_payload("s3cret") == b'{"type":"auth","token":"s3cret"}'


def test_no_websocket_until_ble_open_and_only_one_session(tmp_path):
    machine = _machine(tmp_path)

    async def run():
        audio = fragment(1, TYPE_AUDIO, b"\x01\x02", 20)
        await machine.uplink(audio[0])
        assert machine.connections == 0
        open_msg = b'{"type":"ble_open","device_id":"aa:bb","client_id":"cid","authorization":"Bearer t","protocol_version":"1"}'
        for frag in fragment(2, TYPE_JSON, open_msg, 20):
            await machine.uplink(frag)
        for frag in fragment(3, TYPE_JSON, open_msg, 20):  # a second open must not open a second socket
            await machine.uplink(frag)
        assert machine.connections == 1
        ws = machine._sockets[0]
        assert ws.headers["device-id"] == "aa:bb"
        assert ws.headers["client-id"] == "cid"
        assert ws.headers["authorization"] == "Bearer t"
        assert ws.headers["protocol-version"] == "1"
        assert ws.sent == []  # ble_open itself is not forwarded
        await machine.close()
        assert ws.closed

    asyncio.run(run())


def test_dropped_audio_fragment_does_not_desync_the_turn(tmp_path):
    machine = _machine(tmp_path)

    async def run():
        for frag in fragment(1, TYPE_JSON, b'{"type":"ble_open","device_id":"aa","client_id":"bb","protocol_version":"1"}', 20):
            await machine.uplink(frag)
        first = fragment(2, TYPE_AUDIO, b"A" * 40, 20)
        await machine.uplink(first[0])
        await machine.uplink(first[-1])  # gap in the middle
        for frag in fragment(3, TYPE_AUDIO, b"B" * 30, 20):
            await machine.uplink(frag)
        ws = machine._sockets[0]
        assert ws.sent == [b"B" * 30]
        await machine.close()

    asyncio.run(run())


def test_duplicate_json_is_acked_and_forwarded_once(tmp_path):
    machine = _machine(tmp_path)

    async def run():
        for frag in fragment(1, TYPE_JSON, b'{"type":"ble_open","device_id":"aa","client_id":"bb","protocol_version":"1"}', 20):
            await machine.uplink(frag)
        hello = b'{"type":"hello","transport":"websocket"}'
        for _ in range(2):
            for frag in fragment(9, TYPE_JSON, hello, 20):
                await machine.uplink(frag)
        ws = machine._sockets[0]
        assert ws.sent == [hello.decode()]
        await machine.close()

    asyncio.run(run())


def test_downlink_audio_is_paced_one_frame_per_60ms(tmp_path):
    slept = []

    async def sleep(dt):
        slept.append(dt)

    machine = _machine(tmp_path, sleep=sleep, monotonic=lambda: 0.0)

    async def run():
        await machine._send_audio(b"\x11" * 8)
        await machine._send_audio(b"\x22" * 8)
        audio_writes = [item for item in machine._writes if not item[1]]
        assert len(audio_writes) >= 2
        assert all(response is False for _, response in audio_writes)

    asyncio.run(run())
    assert slept == [AUDIO_FRAME_S]


def test_heartbeat_writes_the_menu_bar_file(tmp_path):
    machine = _machine(tmp_path)
    machine._now = lambda: 1_700_000_000.0
    machine.heartbeat()
    assert (tmp_path / "board_heartbeat").read_text() == "1700000000"


def test_ble_close_closes_the_websocket(tmp_path):
    machine = _machine(tmp_path)

    async def run():
        for frag in fragment(1, TYPE_JSON, b'{"type":"ble_open","device_id":"aa","client_id":"bb","protocol_version":"1"}', 20):
            await machine.uplink(frag)
        for frag in fragment(2, TYPE_JSON, b'{"type":"ble_close"}', 20):
            await machine.uplink(frag)
        assert machine._sockets[0].closed
        assert machine.connections == 1

    asyncio.run(run())
