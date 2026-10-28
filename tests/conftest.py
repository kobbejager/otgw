import pytest

from otgw.app import App
from otgw.config import parse_config
from otgw.protocol import MsgType

CONFIG = """
mqtt:
  prefix: otgw
gateway:
  device: /dev/ttyUSB0
publish:
  refresh: 300
"""


def frame(source: str, msg_type: MsgType, data_id: int, value: int = 0) -> str:
    """A gateway line, e.g. T10100A00 (the parity bit is left 0; it is not checked)."""
    return f"{source}{(msg_type << 28) | (data_id << 16) | (value & 0xFFFF):08X}"


def f88(value: float) -> int:
    return round(value * 256) & 0xFFFF


def read(data_id: int, value: int, request: int = 0) -> list[str]:
    """Thermostat reads, boiler answers."""
    return [frame("T", MsgType.READ_DATA, data_id, request), frame("B", MsgType.READ_ACK, data_id, value)]


def write(data_id: int, value: int) -> list[str]:
    """Thermostat writes, boiler acknowledges."""
    return [frame("T", MsgType.WRITE_DATA, data_id, value), frame("B", MsgType.WRITE_ACK, data_id, value)]


# Starts the next transaction, which completes the previous one.
NEXT = frame("T", MsgType.READ_DATA, 0, 0x0300)


@pytest.fixture
def clock():
    class Clock:
        now = 1000.0

        def __call__(self) -> float:
            return self.now

    return Clock()


@pytest.fixture
def app(clock):
    return App(parse_config(CONFIG, env={}), clock=clock)


def feed(app: App, *lines: str) -> None:
    for line in lines:
        app.handle_line(line)


def drain(app: App) -> list[tuple[str, str]]:
    messages = []
    while not app.queue.empty():
        topic, payload, _ = app.queue.get_nowait()
        messages.append((topic, payload))
    return messages


def commands(app: App) -> list[str]:
    sent = []
    while not app.commands.queue.empty():
        sent.append(app.commands.queue.get_nowait().text)
    return sent
