"""Connection to the gateway (serial or TCP) and sending of commands."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import serial_asyncio_fast

from otgw.config import GatewayConfig
from otgw.protocol import Response

log = logging.getLogger("otgw")

COMMAND_TIMEOUT = 3.0
COMMAND_ATTEMPTS = 3


async def open_gateway(config: GatewayConfig) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    if config.host is not None:
        return await asyncio.open_connection(config.host, config.port)
    return await serial_asyncio_fast.open_serial_connection(url=config.device, baudrate=config.baudrate)


def describe(config: GatewayConfig) -> str:
    return f"{config.host}:{config.port}" if config.host is not None else str(config.device)


@dataclass
class Command:
    text: str  # e.g. TT=20.50
    raw: bool = False  # from the <prefix>/command topic: its response is published

    @property
    def code(self) -> str:
        return self.text[:2].upper()


class Commands:
    """Sends commands one at a time, waiting for each answer (the gateway has a tiny input buffer)."""

    def __init__(self, timeout: float = COMMAND_TIMEOUT, attempts: int = COMMAND_ATTEMPTS):
        self.timeout = timeout
        self.attempts = attempts
        self.queue: asyncio.Queue[Command] = asyncio.Queue()
        self._pending: tuple[Command, asyncio.Future[Response]] | None = None

    def submit(self, text: str, raw: bool = False) -> None:
        self.queue.put_nowait(Command(text, raw))

    def clear(self) -> int:
        dropped = 0
        while not self.queue.empty():
            self.queue.get_nowait()
            dropped += 1
        return dropped

    def handle_response(self, response: Response) -> Command | None:
        """The command this response answers, if one is waiting for it."""
        if self._pending is None:
            return None
        command, future = self._pending
        if response.error is None and response.code != command.code:
            return None
        if not future.done():
            future.set_result(response)
        self._pending = None
        return command

    async def run(self, write: Callable[[str], Awaitable[None]]) -> None:
        loop = asyncio.get_running_loop()
        while True:
            command = await self.queue.get()
            for attempt in range(1, self.attempts + 1):
                future: asyncio.Future[Response] = loop.create_future()
                self._pending = (command, future)
                log.debug("sending %s", command.text)
                await write(command.text + "\r")
                try:
                    async with asyncio.timeout(self.timeout):
                        await future
                    break
                except TimeoutError:
                    log.warning("no answer to %s (attempt %d of %d)", command.text, attempt, self.attempts)
                finally:
                    self._pending = None
