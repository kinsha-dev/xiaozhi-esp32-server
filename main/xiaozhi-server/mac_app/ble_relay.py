"""Relay one BLE voice turn onto the board's websocket, and no other time.

The impersonated socket opens when the board sends `ble_open` and closes on
`ble_close` or when the link drops. Heartbeats write the menu-bar file directly.
Downlink Opus is paced at one frame per 60 ms; the websocket is not read ahead
of that pace, which is the backpressure on the server.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from mac_app.ble_frag import (
    TYPE_ACK,
    TYPE_AUDIO,
    TYPE_JSON,
    fragment,
    send_reliable,
    Reassembler,
)

AUDIO_FRAME_S = 0.06

SERVICE_UUID = "7d2a0001-3c1e-4b4a-9f1a-6e5d4c3b2a10"
TX_UUID = "7d2a0002-3c1e-4b4a-9f1a-6e5d4c3b2a10"       # board -> Mac notify
RX_UUID = "7d2a0003-3c1e-4b4a-9f1a-6e5d4c3b2a10"       # Mac -> board write
HEARTBEAT_UUID = "7d2a0004-3c1e-4b4a-9f1a-6e5d4c3b2a10"


def auth_payload(token: str) -> bytes:
    return json.dumps({"type": "auth", "token": token}, separators=(",", ":")).encode()


class RelayMachine:
    def __init__(self, connect, heartbeat_path, write, token: str = "", mtu_payload: int = 185,
                 sleep=None, now=None, monotonic=None):
        self._connect = connect
        self.heartbeat_path = Path(heartbeat_path)
        self._write = write
        self.token = token
        self.mtu_payload = mtu_payload
        self._sleep = sleep or asyncio.sleep
        self._now = now or time.time
        self._monotonic = monotonic or time.monotonic
        self._up = Reassembler()
        self._ws = None
        self._reader = None
        self._down_seq = 0
        self._acks: asyncio.Queue = asyncio.Queue()
        self._pending: list[bytes] = []
        self._next_audio = 0.0
        self._heart_logged = False
        self.connections = 0

    def _take_seq(self) -> int:
        seq = self._down_seq
        self._down_seq = (self._down_seq + 1) & 0xFF
        return seq

    async def _wait_ack(self, seq: int, timeout: float) -> bool:
        deadline = self._monotonic() + timeout
        while True:
            left = deadline - self._monotonic()
            if left <= 0:
                return False
            try:
                got = await asyncio.wait_for(self._acks.get(), left)
            except asyncio.TimeoutError:
                return False
            if got == seq:
                return True

    async def send_auth(self) -> bool:
        return await send_reliable(
            self._write, self._wait_ack, self._take_seq(), auth_payload(self.token), self.mtu_payload)

    def heartbeat(self):
        self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        self.heartbeat_path.write_text(f"{self._now():.0f}")
        if not self._heart_logged:
            self._heart_logged = True
            print("[ble] heartbeat", flush=True)

    async def uplink(self, packet: bytes):
        msg = self._up.push(packet)
        if msg is None:
            return
        msg_type, payload = msg
        if msg_type == TYPE_ACK:
            if payload:
                await self._acks.put(payload[0])
            return
        if msg_type == TYPE_JSON:
            await self._ack(self._up.last_completed)
            if self._up.duplicate:
                return
            await self._on_json(payload)
            return
        if msg_type == TYPE_AUDIO and not self._up.duplicate and self._ws is not None:
            await self._ws.send(payload)

    async def _ack(self, seq: int | None):
        if seq is None:
            return
        for frag in fragment(self._take_seq(), TYPE_ACK, bytes((seq & 0xFF,)), self.mtu_payload):
            await self._write(frag, True)

    async def _on_json(self, payload: bytes):
        try:
            msg = json.loads(payload)
        except ValueError:
            return
        kind = msg.get("type")
        if kind == "ble_open":
            await self._open(msg)
            return
        if kind == "ble_close":
            await self.close_ws()
            return
        if self._ws is None:
            self._pending.append(payload)
            return
        text = payload.decode()
        await self._ws.send(text)

    async def _open(self, msg: dict):
        if self._ws is not None:
            return
        headers = {
            "device-id": msg.get("device_id") or "",
            "client-id": msg.get("client_id") or "",
            "protocol-version": str(msg.get("protocol_version") or "1"),
        }
        auth = msg.get("authorization") or ""
        if auth:
            headers["authorization"] = auth
        try:
            ws = await self._connect(headers)
        except Exception as exc:
            print(f"[ble] websocket open failed: {exc}", flush=True)
            return
        self._ws = ws
        self.connections += 1
        self._reader = asyncio.create_task(self._read_server(ws))
        pending, self._pending = self._pending, []
        for item in pending:
            await ws.send(item.decode())

    async def _read_server(self, ws):
        try:
            async for raw in ws:
                if self._ws is not ws:
                    return
                if isinstance(raw, bytes):
                    await self._send_audio(raw)
                else:
                    text = raw if isinstance(raw, str) else bytes(raw).decode()
                    await self._send_json(text.encode())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[ble] websocket read ended: {exc}", flush=True)

    async def _send_audio(self, opus: bytes):
        delay = self._next_audio - self._monotonic()
        if delay > 0:
            await self._sleep(delay)
        seq = self._take_seq()
        for frag in fragment(seq, TYPE_AUDIO, opus, self.mtu_payload):
            await self._write(frag, False)
        self._next_audio = self._monotonic() + AUDIO_FRAME_S

    async def _send_json(self, payload: bytes):
        await send_reliable(self._write, self._wait_ack, self._take_seq(), payload, self.mtu_payload)

    async def close_ws(self):
        ws, self._ws = self._ws, None
        task, self._reader = self._reader, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if ws is not None:
            close = getattr(ws, "close", None)
            if close is not None:
                result = close()
                if asyncio.iscoroutine(result):
                    await result

    async def close(self):
        await self.close_ws()
