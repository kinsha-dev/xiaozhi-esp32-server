import asyncio

from mac_app.ble_frag import TYPE_AUDIO, TYPE_JSON, crc8, fragment, send_reliable, Reassembler


def test_golden_fragment_matches_crc_and_length_prefix():
    packet = fragment(7, TYPE_AUDIO, b"ab", 20)
    assert packet == [bytes.fromhex("070001a8000000026162")]
    body = packet[0][4:]
    assert body[:2] == bytes((TYPE_AUDIO, 0))
    assert int.from_bytes(body[2:4], "big") == 2
    assert crc8(packet[0][:3] + body) == packet[0][3]


def test_two_notifies_reassemble_and_a_dropped_fragment_does_not_desync():
    first = fragment(1, TYPE_AUDIO, b"A" * 40, 20)
    second = fragment(2, TYPE_AUDIO, b"B" * 30, 20)
    assert len(first) >= 3  # force a multi-fragment frame at MTU 20
    box = Reassembler()
    assert box.push(first[0]) is None
    assert box.push(first[2]) is None  # middle fragment dropped
    got = None
    for frag in second:
        got = box.push(frag) or got
    assert got == (TYPE_AUDIO, b"B" * 30)


def test_coalesced_notifies_still_reassemble():
    frags = fragment(4, TYPE_JSON, b'{"type":"keepalive"}', 20)
    assert len(frags) > 1
    box = Reassembler()
    got = None
    for frag in frags:  # two notifies, back to back
        got = box.push(frag) or got
    assert got == (TYPE_JSON, b'{"type":"keepalive"}')


def test_bad_crc_is_ignored():
    frags = fragment(1, TYPE_AUDIO, b"hello", 40)
    bad = bytearray(frags[0])
    bad[4] ^= 0xFF
    assert Reassembler().push(bytes(bad)) is None


def test_json_retry_recovers_a_dropped_copy():
    delivered = []
    writes = []

    async def write(frag, response):
        writes.append(bytes(frag))
        if len(writes) > 1:  # first attempt is dropped
            delivered.append(bytes(frag))

    async def wait_ack(seq, timeout):
        box = Reassembler()
        got = None
        for frag in delivered:
            got = box.push(frag) or got
        delivered.clear()
        return got == (TYPE_JSON, b'{"type":"tts","state":"stop"}') and seq == 3

    ok = asyncio.run(send_reliable(write, wait_ack, 3, b'{"type":"tts","state":"stop"}', 40, attempts=3, timeout=0.01))
    assert ok
    assert len(writes) == 2  # one dropped attempt, then one that landed
