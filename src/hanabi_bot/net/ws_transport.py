"""Async WebSocket transport for hanab.live.

Port of scala-bot/src/scala_bot/bot.scala's WebSocket loop.

Architecture:
- A receive loop reads frames, decodes, and dispatches to the BotClient.
- A send queue holds outbound messages; a sender task drains it with 500ms spacing
  (rate-limit-safe: hanab.live allows 100 msgs/2s).
- Reconnect with exponential backoff on dropped connections.

To keep the bot logic synchronous (state machine + reactor are CPU-bound), only the
network I/O runs on the asyncio event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection, connect

from . import codec

log = logging.getLogger("hanabi_bot.transport")


class WebSocketClosedError(RuntimeError):
    pass


class BotTransport:
    """Owns the WebSocket connection, send queue, and reconnect loop.

    `on_message(command, payload)` is the bot's command dispatcher. It may be
    sync (called immediately) or async (awaited).
    """

    def __init__(
        self,
        ws_url: str,
        cookie: str,
        on_message: Callable[[str, dict[str, Any]], Awaitable[None] | None],
        *,
        send_interval: float = 0.5,
        max_retries: int = 5,
    ) -> None:
        self.ws_url = ws_url
        self.cookie = cookie
        self.on_message = on_message
        self.send_interval = send_interval
        self.max_retries = max_retries
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._connected = asyncio.Event()
        self._stop = asyncio.Event()

    def queue_send(self, command: str, payload: dict[str, Any] | None = None) -> None:
        """Enqueue an outbound message. Non-blocking."""
        msg = codec.encode(command, payload)
        log.debug("-> %s", msg)
        self._queue.put_nowait(msg)

    async def stop(self) -> None:
        self._stop.set()

    async def _run_one_connection(self) -> None:
        """Open a single WebSocket, run send + receive in parallel until closed."""
        headers = [("Cookie", self.cookie)]
        async with connect(self.ws_url, additional_headers=headers) as ws:
            self._connected.set()
            print(f"connected to {self.ws_url}")
            sender = asyncio.create_task(self._sender(ws))
            try:
                await self._receiver(ws)
            finally:
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sender

    async def _sender(self, ws: ClientConnection) -> None:
        try:
            while not self._stop.is_set():
                msg = await self._queue.get()
                await ws.send(msg)
                await asyncio.sleep(self.send_interval)
        except asyncio.CancelledError:
            raise
        except websockets.exceptions.ConnectionClosed:
            return

    async def _receiver(self, ws: ClientConnection) -> None:
        buffer: list[str] = []
        async for frame in ws:
            if self._stop.is_set():
                break
            # frame is either str (text) or bytes (binary); hanab.live uses text.
            if isinstance(frame, bytes):
                frame = frame.decode("utf-8")
            buffer.append(frame)
            full = "".join(buffer)
            buffer.clear()
            log.debug("<- %s", full)
            try:
                command, payload = codec.decode(full)
            except ValueError as e:
                log.warning("failed to decode WS message: %s", e)
                continue
            result = self.on_message(command, payload)
            if asyncio.iscoroutine(result):
                await result

    async def run(self) -> None:
        """Run the transport with reconnect/backoff until stop() is called."""
        attempt = 0
        while not self._stop.is_set():
            self._connected.clear()
            try:
                await self._run_one_connection()
                # Clean close -> reset attempt counter.
                attempt = 0
            except (
                websockets.exceptions.ConnectionClosed,
                websockets.exceptions.InvalidStatus,
                ConnectionRefusedError,
                OSError,
                WebSocketClosedError,
            ) as e:
                was_connected = self._connected.is_set()
                if attempt >= self.max_retries:
                    print(f"WS giving up after {attempt} retries: {e}")
                    raise
                attempt = 0 if was_connected else attempt + 1
                delay = 2 ** attempt
                print(f"WS connection lost (attempt {attempt}/{self.max_retries}): {e}; retrying in {delay}s")
                if self._stop.is_set():
                    break
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                    break
                except TimeoutError:
                    pass
