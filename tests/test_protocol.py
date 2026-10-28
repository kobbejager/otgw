from conftest import f88, frame

from otgw.protocol import (
    MsgType,
    Response,
    Transactions,
    decode,
    parse_frame,
    parse_override,
    parse_response,
)


def test_parse_frame():
    parsed = parse_frame("T80190000")  # parity bit set, READ_DATA, id 25
    assert (parsed.source, parsed.msg_type, parsed.data_id, parsed.value) == ("T", MsgType.READ_DATA, 25, 0)
    parsed = parse_frame("BC0193A00")
    assert (parsed.msg_type, parsed.data_id, parsed.value) == (MsgType.READ_ACK, 25, 0x3A00)
    assert parse_frame("E80190000") is None  # parity error
    assert parse_frame("T3019000") is None
    assert parse_frame("TT: 20.00") is None


def test_parse_response():
    assert parse_response("TT: 19.13") == Response("TT", "19.13")
    assert parse_response("PR: O=N") == Response("PR", "O=N")
    assert parse_response("BV") == Response("BV", error="bad value")
    assert parse_response("OpenTherm Gateway 6.5") is None


def test_decode_temperatures_are_signed():
    assert decode(27, f88(-3.5)) == [("outside_temperature", -3.5)]
    assert decode(25, f88(58.25)) == [("boiler_water_temperature", 58.25)]
    assert decode(33, 0xFFF6) == [("exhaust_temperature", -10)]
    assert decode(116, 41234) == [("burner_starts", 41234)]
    assert decode(200, 1) == []


def test_decode_status_and_faults():
    values = dict(decode(0, 0x030A))  # master: ch + dhw enabled; slave: ch active + flame
    assert values["ch_enabled"] == 1 and values["dhw_enabled"] == 1 and values["otc_active"] == 0
    assert values["ch_active"] == 1 and values["flame_on"] == 1 and values["dhw_active"] == 0
    values = dict(decode(5, 0x0415))
    assert values["low_water_pressure"] == 1 and values["service_request"] == 0
    assert values["oem_fault_code"] == 0x15


def transactions(*lines: str):
    collector = Transactions()
    done = []
    for line in lines:
        done += collector.feed(parse_frame(line))
    return done, collector


def test_read_uses_boiler_answer():
    done, collector = transactions(
        frame("T", MsgType.READ_DATA, 25), frame("B", MsgType.READ_ACK, 25, f88(60))
    )
    assert done == []  # an answer from the gateway may still follow
    done, _ = transactions(
        frame("T", MsgType.READ_DATA, 25),
        frame("B", MsgType.READ_ACK, 25, f88(60)),
        frame("T", MsgType.READ_DATA, 26),
    )
    assert done[0].data() == (25, f88(60))


def test_read_answered_by_gateway():
    # outside temperature set with OT=: the boiler does not know it, the gateway answers the thermostat
    done, _ = transactions(
        frame("T", MsgType.READ_DATA, 27),
        frame("B", MsgType.UNKNOWN_DATAID, 27),
        frame("A", MsgType.READ_ACK, 27, f88(4.5)),
    )
    assert done[0].data() == (27, f88(4.5))


def test_write_uses_request_as_sent_to_the_boiler():
    # control setpoint override (CS=): the gateway replaces the thermostat's request
    done, _ = transactions(
        frame("T", MsgType.WRITE_DATA, 1, f88(40)),
        frame("R", MsgType.WRITE_DATA, 1, f88(55)),
        frame("B", MsgType.WRITE_ACK, 1, f88(55)),
        frame("A", MsgType.WRITE_ACK, 1, f88(40)),
    )
    assert done[0].data() == (1, f88(55))
    # room temperature: published even when the boiler does not support it
    done, _ = transactions(
        frame("T", MsgType.WRITE_DATA, 24, f88(20.5)),
        frame("B", MsgType.UNKNOWN_DATAID, 24, f88(20.5)),
        frame("T", MsgType.READ_DATA, 0),
    )
    assert done[0].data() == (24, f88(20.5))


def test_status_combines_master_and_boiler_flags():
    # HW=0: the gateway clears the DHW enable bit towards the boiler, and restores it for the thermostat
    done, _ = transactions(
        frame("T", MsgType.READ_DATA, 0, 0x0300),
        frame("R", MsgType.READ_DATA, 0, 0x0100),
        frame("B", MsgType.READ_ACK, 0, 0x010A),
        frame("A", MsgType.READ_ACK, 0, 0x030A),
    )
    assert done[0].data() == (0, 0x010A)


def test_gateway_requests_and_alternatives():
    # the gateway asks something else instead (alternative data id): the boiler's answer counts
    done, _ = transactions(
        frame("T", MsgType.READ_DATA, 20),
        frame("R", MsgType.READ_DATA, 28),
        frame("B", MsgType.READ_ACK, 28, f88(35)),
        frame("A", MsgType.UNKNOWN_DATAID, 20),
    )
    assert done[0].data() == (28, f88(35))
    # a request of the gateway itself, after a complete transaction
    done, collector = transactions(
        frame("T", MsgType.READ_DATA, 25),
        frame("B", MsgType.READ_ACK, 25, f88(60)),
        frame("R", MsgType.WRITE_DATA, 56, f88(55)),
        frame("B", MsgType.WRITE_ACK, 56, f88(55)),
    )
    assert done[0].data() == (25, f88(60))
    assert collector.current.data() == (56, f88(55))


def test_invalid_answers_carry_no_value():
    done, _ = transactions(
        frame("T", MsgType.READ_DATA, 33),
        frame("B", MsgType.UNKNOWN_DATAID, 33),
        frame("T", MsgType.READ_DATA, 0),
    )
    assert done[0].data() is None


def test_parse_override():
    assert parse_override("O=N") == ("off", None)
    assert parse_override("O=T20.50") == ("temporary", 20.5)
    assert parse_override("O=c18.00") == ("constant", 18.0)
    assert parse_override("O=X") is None
