"""The serial output of the OpenTherm Gateway (Schelte Bron's PIC firmware).

https://otgw.tclcode.com/firmware.html

The gateway sits between thermostat and boiler and reports every OpenTherm frame as a line of
one letter and 8 hex digits:

- ``T``: received from the thermostat;
- ``R``: request sent to the boiler, when the gateway changed or replaced the thermostat's request;
- ``B``: received from the boiler;
- ``A``: answer returned to the thermostat, when the gateway changed or replaced the boiler's answer;
- ``E``: parity or stop bit error.

Commands are answered with ``XX: value`` (the command code and the value as the gateway understood
it), or with a two-letter error code.
"""

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import IntEnum

FRAME = re.compile(r"^([TRBAE])([0-9A-F]{8})$")
RESPONSE = re.compile(r"^([A-Z]{2}): ?(.*)$")
VERSION = re.compile(r"OpenTherm Gateway (\S+)")

ERRORS = {
    "NG": "unknown command",
    "SE": "syntax error",
    "BV": "bad value",
    "OR": "out of range",
    "NS": "no space",
    "NF": "not found",
    "OE": "overrun error",
}


class MsgType(IntEnum):
    READ_DATA = 0
    WRITE_DATA = 1
    INVALID_DATA = 2
    READ_ACK = 4
    WRITE_ACK = 5
    DATA_INVALID = 6
    UNKNOWN_DATAID = 7


@dataclass(frozen=True)
class Frame:
    source: str  # T, R, B or A
    msg_type: MsgType
    data_id: int
    value: int  # the 16 data bits

    @property
    def hi(self) -> int:
        return self.value >> 8

    @property
    def lo(self) -> int:
        return self.value & 0xFF


@dataclass(frozen=True)
class Response:
    code: str  # the command code, e.g. TT; or the error code (SE, BV, ...) with error set
    value: str = ""
    error: str | None = None


def parse_frame(line: str) -> Frame | None:
    match = FRAME.match(line)
    if match is None or match[1] == "E":
        return None
    raw = int(match[2], 16)
    msg_type = (raw >> 28) & 7
    if msg_type == 3:  # reserved
        return None
    return Frame(match[1], MsgType(msg_type), (raw >> 16) & 0xFF, raw & 0xFFFF)


def parse_response(line: str) -> Response | None:
    if line in ERRORS:
        return Response(line, error=ERRORS[line])
    match = RESPONSE.match(line)
    if match is None:
        return None
    return Response(match[1], match[2].strip())


# --- data values --------------------------------------------------------------------------------


def f88(value: int) -> float:
    """Signed fixed point, 1/256 units (temperatures, setpoints, percentages)."""
    if value >= 0x8000:
        value -= 0x10000
    return round(value / 256, 2)


def s16(value: int) -> int:
    return value - 0x10000 if value >= 0x8000 else value


def u16(value: int) -> int:
    return value


def flag_values(names: dict[int, str], bits: int) -> Iterator[tuple[str, int]]:
    for bit, name in names.items():
        yield name, (bits >> bit) & 1


MASTER_STATUS = {0: "ch_enabled", 1: "dhw_enabled", 2: "cooling_enabled", 3: "otc_active", 4: "ch2_enabled"}
SLAVE_STATUS = {
    0: "fault",
    1: "ch_active",
    2: "dhw_active",
    3: "flame_on",
    4: "cooling_active",
    5: "ch2_active",
    6: "diagnostic_indication",
}
FAULT_FLAGS = {
    0: "service_request",
    1: "lockout_reset",
    2: "low_water_pressure",
    3: "gas_flame_fault",
    4: "air_pressure_fault",
    5: "water_over_temperature",
}

# Data id -> (name, decoder) for single values.
SIMPLE: dict[int, tuple[str, Callable[[int], float | int]]] = {
    1: ("control_setpoint", f88),
    8: ("control_setpoint_2", f88),
    9: ("remote_override_setpoint", f88),
    14: ("max_relative_modulation_level", f88),
    16: ("room_setpoint", f88),
    17: ("relative_modulation_level", f88),
    18: ("ch_water_pressure", f88),
    19: ("dhw_flow_rate", f88),
    23: ("room_setpoint_2", f88),
    24: ("room_temperature", f88),
    25: ("boiler_water_temperature", f88),
    26: ("dhw_temperature", f88),
    27: ("outside_temperature", f88),
    28: ("return_water_temperature", f88),
    31: ("boiler_water_temperature_2", f88),
    33: ("exhaust_temperature", s16),
    56: ("dhw_setpoint", f88),
    57: ("max_ch_water_setpoint", f88),
    115: ("oem_diagnostic_code", u16),
    116: ("burner_starts", u16),
    117: ("ch_pump_starts", u16),
    118: ("dhw_pump_starts", u16),
    119: ("dhw_burner_starts", u16),
    120: ("burner_operation_hours", u16),
    121: ("ch_pump_operation_hours", u16),
    122: ("dhw_pump_valve_operation_hours", u16),
    123: ("dhw_burner_operation_hours", u16),
}

STATUS = 0
FAULTS = 5

KNOWN_IDS = frozenset({STATUS, FAULTS, *SIMPLE})

VALUE_NAMES: tuple[str, ...] = (
    *MASTER_STATUS.values(),
    *SLAVE_STATUS.values(),
    *FAULT_FLAGS.values(),
    "oem_fault_code",
    *(name for name, _ in SIMPLE.values()),
)


def decode(data_id: int, value: int) -> list[tuple[str, float | int]]:
    """The named values in one data value."""
    if data_id == STATUS:
        return [*flag_values(MASTER_STATUS, value >> 8), *flag_values(SLAVE_STATUS, value & 0xFF)]
    if data_id == FAULTS:
        return [*flag_values(FAULT_FLAGS, value >> 8), ("oem_fault_code", value & 0xFF)]
    if data_id in SIMPLE:
        name, decoder = SIMPLE[data_id]
        return [(name, decoder(value))]
    return []


# --- transactions -------------------------------------------------------------------------------


@dataclass
class Transaction:
    """One request from the thermostat (or the gateway itself) and its answers."""

    request: Frame  # the request as the boiler received it (R replaces T)
    response: Frame | None = None  # from the boiler
    answer: Frame | None = None  # from the gateway to the thermostat, when it replaced the response

    def data(self) -> tuple[int, int] | None:
        """(data id, value) of the boiler's view of this transaction, if it carries a value.

        - Writes (room temperature, room setpoint, control setpoint, ...): what the boiler was sent.
        - Reads: the gateway's answer to the thermostat if it gave one (remote override setpoint,
          an outside temperature set with OT=, ...), otherwise the boiler's.
        - Status (id 0): master flags from the request, slave flags from the boiler.
        """
        request = self.request
        if request.msg_type == MsgType.WRITE_DATA:
            return request.data_id, request.value
        if request.msg_type != MsgType.READ_DATA:
            return None
        response = self.response
        boiler = response if response and response.msg_type == MsgType.READ_ACK else None
        if boiler is not None and boiler.data_id != request.data_id:
            boiler = None
        if request.data_id == STATUS:
            if boiler is None:
                return None
            return STATUS, (request.value & 0xFF00) | boiler.lo
        answer = self.answer
        if answer and answer.msg_type == MsgType.READ_ACK and answer.data_id == request.data_id:
            return answer.data_id, answer.value
        if boiler is not None:
            return boiler.data_id, boiler.value
        return None


class Transactions:
    """Groups the T, R, B and A frames of one exchange.

    A transaction is complete when its answer arrives, or when the next request starts (an answer is
    only sent when the gateway changes the response).
    """

    def __init__(self) -> None:
        self.current: Transaction | None = None

    def feed(self, frame: Frame) -> list[Transaction]:
        done: list[Transaction] = []
        current = self.current
        if frame.source == "T":
            if current is not None:
                done.append(current)
            self.current = Transaction(frame)
        elif frame.source == "R":
            if current is not None and current.request.source == "T" and current.response is None:
                current.request = frame  # the gateway changed the thermostat's request
            else:  # a request of the gateway itself
                if current is not None:
                    done.append(current)
                self.current = Transaction(frame)
        elif frame.source == "B":
            if current is not None and current.response is None:
                current.response = frame
        elif frame.source == "A" and current is not None:
            current.answer = frame
            done.append(current)
            self.current = None
        return done


# --- reports --------------------------------------------------------------------------------------

OVERRIDE_MODES = {"N": "off", "T": "temporary", "C": "constant"}
DHW_MODES = {"A": "auto", "T": "auto", "0": "off", "1": "on"}


def parse_override(value: str) -> tuple[str, float | None] | None:
    """`PR=O` report (``O=N``, ``O=T20.50``, ``O=C18.00``) -> (mode, setpoint)."""
    match = re.fullmatch(r"O=([NTCntc])([0-9.]*)", value)
    if match is None:
        return None
    mode = OVERRIDE_MODES[match[1].upper()]
    if mode == "off":
        return mode, None
    try:
        return mode, float(match[2])
    except ValueError:
        return None
