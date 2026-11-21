import asyncio
import json

from core import board_link
from mac_app.ble_relay import RelayMachine


def test_message_maps_modes(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_BOX_DATA", str(tmp_path))
    assert board_link.read_mode() == "auto"
    (tmp_path / "board_link").write_text("bluetooth")
    assert board_link.read_mode() == "bluetooth"
    assert json.loads(board_link.message("bluetooth")) == {"type": "system", "command": "link_bluetooth"}
    (tmp_path / "board_link").write_text("junk")
    assert board_link.read_mode() == "auto"


def test_relay_sends_system_command_to_board(tmp_path):
    sent = []

    async def write(data, response):
        sent.append(data)

    machine = RelayMachine(None, tmp_path / "hb", write, mtu_payload=185)

    async def run():
        task = asyncio.create_task(machine.send_system("link_bluetooth"))
        await asyncio.sleep(0.05)
        await machine._acks.put(0)  # the board acks seq 0
        return await task

    assert asyncio.run(run()) is True
    body = sent[0][4:]
    assert body[0] == 1 and json.loads(body[4:]) == {"type": "system", "command": "link_bluetooth"}
