"""Self-contained BLE fragments for one BinaryProtocol3 frame.

Each ATT payload is one fragment, so a dropped or coalesced notify cannot
shift the next frame:

    seq u8, index u8, count u8, crc8 u8, slice

crc8 covers seq, index, count, and the slice (poly 0x07, init 0).
The reassembled body is:

    type u8, reserved u8, payload_size u16 big-endian, payload

type 0 is Opus, 1 is JSON, 2 is an ack whose payload is the one-byte seq.
"""
from __future__ import annotations

HEADER = 4
TYPE_AUDIO = 0
TYPE_JSON = 1
TYPE_ACK = 2


def crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def fragment(seq: int, msg_type: int, payload: bytes, mtu_payload: int) -> list[bytes]:
    if mtu_payload < HEADER + 1:
        raise ValueError(f"mtu payload {mtu_payload} cannot hold a fragment")
    body = bytes((msg_type & 0xFF, 0)) + len(payload).to_bytes(2, "big") + payload
    slice_len = mtu_payload - HEADER
    count = (len(body) + slice_len - 1) // slice_len
    if count > 255:
        raise ValueError("message does not fit in 255 fragments")
    out = []
    for index in range(count):
        sl = body[index * slice_len:(index + 1) * slice_len]
        head = bytes((seq & 0xFF, index, count))
        out.append(head + bytes((crc8(head + sl),)) + sl)
    return out


class Reassembler:
    def __init__(self):
        self._seq = None
        self._count = 0
        self._parts: dict[int, bytes] = {}
        self.last_completed: int | None = None
        self.duplicate = False

    def push(self, packet: bytes) -> tuple[int, bytes] | None:
        """Return (type, payload) when a message completes. A repeat of the last
        completed seq sets `duplicate` and still returns the payload so the
        caller can ack it again."""
        self.duplicate = False
        if len(packet) < HEADER + 1:
            return None
        seq, index, count, crc = packet[0], packet[1], packet[2], packet[3]
        sl = packet[HEADER:]
        if count == 0 or index >= count or crc8(packet[:3] + sl) != crc:
            return None
        if seq != self._seq:
            self._seq = seq
            self._count = count
            self._parts = {}
        if count != self._count:
            return None
        self._parts[index] = sl
        if len(self._parts) != self._count or any(i not in self._parts for i in range(self._count)):
            return None
        body = b"".join(self._parts[i] for i in range(self._count))
        self._parts = {}
        if len(body) < 4:
            return None
        size = int.from_bytes(body[2:4], "big")
        if len(body) != 4 + size:
            return None
        if self.last_completed == seq:
            self.duplicate = True
        else:
            self.last_completed = seq
        return body[0], body[4:]


async def send_reliable(write, wait_ack, seq: int, payload: bytes, mtu_payload: int,
                        attempts: int = 3, timeout: float = 0.5) -> bool:
    """Send one JSON message. `write(fragment, response)` and `wait_ack(seq, timeout)`
    are async. A lost copy is sent again until an ack or the attempts run out."""
    frags = fragment(seq, TYPE_JSON, payload, mtu_payload)
    for _ in range(attempts):
        for frag in frags:
            await write(frag, True)
        if await wait_ack(seq, timeout):
            return True
    return False
