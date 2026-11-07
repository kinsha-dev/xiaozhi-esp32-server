"""Bleak central. Runs in the menu-bar process and reconnects after sleep or range loss.

The board is the peripheral. This process sends the websocket token as the first
JSON message; the board drops any other central. The negotiated MTU is whatever
CoreBluetooth reports — bleak cannot force it.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import threading
from pathlib import Path

from mac_app.ble_relay import HEARTBEAT_UUID, RX_UUID, SERVICE_UUID, TX_UUID, RelayMachine


def ensure_bluetooth_usage(app=Path.home() / "Applications/Vibi.app"):
    """The signed launcher is what macOS asks for Bluetooth, same as the microphone."""
    plist = app / "Contents/Info.plist"
    if not plist.exists():
        return
    text = plist.read_text()
    if "NSBluetoothAlwaysUsageDescription" in text:
        return
    text = text.replace(
        "</dict>",
        "<key>NSBluetoothAlwaysUsageDescription</key>"
        "<string>Vibi uses Bluetooth to talk to the voice board when Wi-Fi is down.</string>\n</dict>",
        1,
    )
    plist.write_text(text)
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=False)


class BleService:
    def __init__(self, cfg):
        self.cfg = cfg
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, name="ble-fallback", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        try:
            asyncio.run(run_until_stopped(self.cfg, self._stop))
        except Exception as exc:
            print(f"[ble] stopped: {exc}", flush=True)


async def run_until_stopped(cfg, stop: threading.Event):
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError:
        print("[ble] bleak is not installed; BLE fallback is off", flush=True)
        return
    token = os.environ.get("VIBI_BLE_TOKEN", "")
    while not stop.is_set():
        try:
            device = await BleakScanner.find_device_by_filter(_is_board, timeout=2.0)
        except Exception as exc:
            print(f"[ble] scan failed: {exc}", flush=True)
            await asyncio.sleep(5)
            continue
        if device is None or stop.is_set():
            continue
        try:
            await _session(BleakClient, device, cfg, token, stop)
        except Exception as exc:
            print(f"[ble] session ended: {exc}", flush=True)
        await asyncio.sleep(1)


def _is_board(_device, adv) -> bool:
    return SERVICE_UUID in [u.lower() for u in (adv.service_uuids or [])]


async def _session(client_cls, device, cfg, token: str, stop: threading.Event):
    from websockets.asyncio.client import connect as ws_connect

    loop = asyncio.get_running_loop()
    incoming: asyncio.Queue = asyncio.Queue()

    def on_disconnect(_client):
        loop.call_soon_threadsafe(incoming.put_nowait, None)

    async with client_cls(device, disconnected_callback=on_disconnect) as client:
        mtu = getattr(client, "mtu_size", 0) or 23
        mtu_payload = max(20, mtu - 3)
        print(f"[ble] connected mtu={mtu} payload={mtu_payload}", flush=True)

        async def write(data: bytes, response: bool):
            await client.write_gatt_char(RX_UUID, data, response=response)

        async def connect(headers: dict):
            return await ws_connect(cfg.server_url, additional_headers=headers, open_timeout=5, max_size=None)

        machine = RelayMachine(
            connect, cfg.data_dir / "board_heartbeat", write, token=token, mtu_payload=mtu_payload)

        def on_tx(_char, data: bytearray):
            loop.call_soon_threadsafe(incoming.put_nowait, bytes(data))

        def on_heart(_char, _data: bytearray):
            loop.call_soon_threadsafe(machine.heartbeat)

        await client.start_notify(TX_UUID, on_tx)
        await client.start_notify(HEARTBEAT_UUID, on_heart)

        async def consume():
            while not stop.is_set():
                try:
                    packet = await asyncio.wait_for(incoming.get(), 0.5)
                except asyncio.TimeoutError:
                    continue
                if packet is None:
                    return
                await machine.uplink(packet)

        consumer = asyncio.create_task(consume())
        try:
            if not await machine.send_auth():
                print("[ble] board rejected the token", flush=True)
                return
            await consumer
        finally:
            consumer.cancel()
            try:
                await consumer
            except (asyncio.CancelledError, Exception):
                pass
            await machine.close()
