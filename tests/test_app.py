import asyncio
import json

from conftest import NEXT, commands, drain, f88, feed, frame, read, write

from otgw.gateway import Commands
from otgw.protocol import MsgType, Response


def test_values_are_published_on_change(app, clock):
    feed(app, *write(24, f88(20.5)), NEXT)
    assert drain(app) == [("otgw/availability", "online"), ("otgw/room_temperature", "20.5")]

    feed(app, *write(24, f88(20.5)), NEXT)
    assert drain(app) == []  # unchanged
    feed(app, *write(24, f88(20.62)), NEXT)
    assert drain(app) == [("otgw/room_temperature", "20.62")]

    clock.now += 300  # refresh: unchanged values again
    feed(app, *write(24, f88(20.62)), NEXT)
    assert drain(app) == [("otgw/room_temperature", "20.62")]


def test_status_bits(app):
    feed(app, *read(0, 0x030A, request=0x0300), NEXT)
    published = dict(drain(app))
    assert published["otgw/flame_on"] == "1"
    assert published["otgw/ch_active"] == "1"
    assert published["otgw/dhw_active"] == "0"
    assert published["otgw/ch_enabled"] == "1"
    assert published["otgw/fault"] == "0"


def test_unknown_lines_and_ids_are_ignored(app):
    feed(app, *read(3, 0x0101), NEXT, "E80190000", "garbage", "")
    assert drain(app) == [("otgw/availability", "online")]


def test_override_from_report(app):
    feed(app, *write(16, f88(19)), NEXT)
    assert ("otgw/target_temperature", "19.0") in drain(app)

    app.handle_line("PR: O=T21.50")
    assert drain(app) == [("otgw/override_mode", "temporary"), ("otgw/target_temperature", "21.5")]

    app.handle_line("PR: O=N")
    assert drain(app) == [("otgw/override_mode", "off"), ("otgw/target_temperature", "19.0")]


def test_override_from_command_response(app):
    app.handle_line("TC: 18.00")
    assert drain(app) == [("otgw/override_mode", "constant"), ("otgw/target_temperature", "18.0")]
    app.handle_line("TT: 0.00")
    assert ("otgw/override_mode", "off") in drain(app)


def test_remote_override_setpoint_triggers_a_query(app, clock):
    app.handle_line("PR: O=T21.00")
    drain(app)
    # the thermostat program changed: the gateway ended the temporary override
    feed(app, *read(9, 0), NEXT)
    assert commands(app) == ["PR=O"]
    feed(app, *read(9, 0), NEXT)
    assert commands(app) == []  # rate limited
    clock.now += 10
    feed(app, *read(9, 0), NEXT)
    assert commands(app) == ["PR=O"]
    app.handle_line("PR: O=N")
    feed(app, *read(9, 0), NEXT)
    assert commands(app) == []  # consistent now


def test_target_temperature_commands(app):
    app.handle_message("otgw/target_temperature/set", b"21.5")
    assert commands(app) == ["TT=21.50"]
    app.handle_line("TC: 20.00")
    app.handle_message("otgw/target_temperature/set", b"21")
    assert commands(app) == ["TC=21.00"]  # stays constant
    app.handle_message("otgw/target_temperature/set", b"45")
    app.handle_message("otgw/target_temperature/set", b"warm")
    assert commands(app) == []


def test_override_mode_commands(app):
    app.handle_message("otgw/override_mode/set", b"temporary")
    assert commands(app) == []  # no target known yet
    feed(app, *write(16, f88(19.5)), NEXT)
    app.handle_message("otgw/override_mode/set", b"constant")
    app.handle_message("otgw/override_mode/set", b"none")
    app.handle_message("otgw/override_mode/set", b"off")
    assert commands(app) == ["TC=19.50", "TT=0", "TT=0"]


def test_other_commands(app):
    for topic, payload in [
        ("dhw_mode", b"auto"),
        ("dhw_mode", b"off"),
        ("dhw_setpoint", b"55"),
        ("max_ch_water_setpoint", b"70"),
        ("outside_temperature", b"-2.5"),
        ("outside_temperature", b""),
        ("max_relative_modulation_level", b"80"),
        ("max_relative_modulation_level", b"auto"),
        ("ch_enabled", b"0"),
        ("control_setpoint", b"0"),
        ("room_temperature", b"20"),  # not settable
    ]:
        app.handle_message(f"otgw/{topic}/set", payload)
    assert commands(app) == [
        "HW=T",
        "HW=0",
        "SW=55.00",
        "SH=70.00",
        "OT=-2.50",
        "OT=-",
        "MM=80",
        "MM=T",
        "CH=0",
        "CS=0.00",
    ]


def test_retained_commands_are_ignored(app):
    app.handle_message("otgw/target_temperature/set", b"21", retain=True)
    app.handle_message("otgw/command", b"GW=0", retain=True)
    assert commands(app) == []


async def test_raw_command_response_is_published(app):
    app.handle_message("otgw/command", b"PR=M")
    command = app.commands.queue.get_nowait()
    assert (command.text, command.raw) == ("PR=M", True)
    app.commands._pending = (command, asyncio.get_running_loop().create_future())  # as if sent
    app.handle_line("PR: M=G")
    assert drain(app) == [("otgw/command/response", "PR: M=G")]


def test_dhw_mode(app):
    app.handle_line("PR: W=A")
    assert drain(app) == [("otgw/dhw_mode", "auto")]
    app.handle_line("HW: 1")
    assert drain(app) == [("otgw/dhw_mode", "on")]


def test_gateway_reset_and_version(app):
    app.handle_line("OpenTherm Gateway 6.5")
    assert commands(app) == ["PR=O", "PR=W"]
    messages = drain(app)
    payload = json.loads(dict(messages)["homeassistant/device/otgw/config"])
    assert payload["device"]["sw_version"] == "6.5"
    app.handle_line("PR: A=OpenTherm Gateway 6.5")
    assert drain(app) == []  # same version: no new discovery


def test_discovery(app):
    payload = app.discovery()["homeassistant/device/otgw/config"]
    assert payload["device"]["identifiers"] == ["otgw"]
    assert [a["topic"] for a in payload["availability"]] == ["otgw/status", "otgw/availability"]
    components = payload["components"]
    climate = components["thermostat"]
    assert climate["current_temperature_topic"] == "otgw/room_temperature"
    assert climate["temperature_command_topic"] == "otgw/target_temperature/set"
    assert climate["preset_modes"] == ["Tijdelijk", "Permanent"]
    assert climate["unique_id"] == "otgw_thermostat"
    assert climate["default_entity_id"] == "climate.otgw_thermostat"
    assert components["room_temperature"]["state_topic"] == "otgw/room_temperature"
    assert components["flame_on"]["platform"] == "binary_sensor"
    assert components["dhw_setpoint"]["command_topic"] == "otgw/dhw_setpoint/set"
    assert components["dhw_mode"]["options"] == ["Thermostaat", "Aan", "Uit"]
    assert [a["topic"] for a in components["gateway"]["availability"]] == ["otgw/status"]
    # every state topic is one the bridge publishes
    topics = {c.get("state_topic") for c in components.values()} - {None}
    assert topics <= app.expected_topics()


def test_publish_all_clears_stale_topics(app):
    ours_old = json.dumps({"origin": {"name": "otgw"}}).encode()
    for topic, payload in [
        ("otgw/value", b"online"),  # v1 bridge status
        ("otgw/value/room_temperature", b"20.1"),  # v1 values
        ("otgw/value/override_mode", b"off"),
        ("otgw/set/room_setpoint/temporary", b"21"),  # a retained v1 command
        ("otgw/room_temperature", b"20.1"),  # still used
        ("otgw/old", b""),  # already cleared
        ("homeassistant/device/otgw_old/config", ours_old),  # ours, old object id
        ("homeassistant/device/otgw/config", b"{}"),  # current
        ("homeassistant/device/elektro_x/config", b"{}"),  # not ours
    ]:
        app.apply_retained(topic, payload)
    app.publish_all()
    messages = drain(app)
    cleared = [topic for topic, payload in messages if payload == ""]
    assert cleared == [
        "homeassistant/device/otgw_old/config",
        "otgw/set/room_setpoint/temporary",
        "otgw/value",
        "otgw/value/override_mode",
        "otgw/value/room_temperature",
    ]
    assert messages[-1] == ("otgw/status", "online")
    app.publish_all()
    assert not [topic for topic, payload in drain(app) if payload == ""]


def test_home_assistant_birth_republishes(app):
    app.handle_message("homeassistant/status", b"online")
    assert any(topic.startswith("homeassistant/device/") for topic, _ in drain(app))


def test_republish_publishes_unchanged_values(app):
    feed(app, *write(24, f88(20.5)), NEXT)
    drain(app)
    app.handle_message("otgw/bridge/republish", b"PRESS")
    drain(app)
    feed(app, *write(24, f88(20.5)), NEXT)
    assert drain(app) == [("otgw/room_temperature", "20.5")]


async def test_commands_wait_for_their_answer():
    sent: list[str] = []
    commands = Commands(timeout=0.05, attempts=2)

    async def write(data: str) -> None:
        sent.append(data)

    runner = asyncio.create_task(commands.run(write))
    commands.submit("TT=20.00")
    commands.submit("PR=O")
    await asyncio.sleep(0.01)
    assert sent == ["TT=20.00\r"]  # the second waits for the first
    assert commands.handle_response(Response("PR", "O=N")) is None  # not the answer to TT
    assert commands.handle_response(Response("TT", "20.00")).text == "TT=20.00"
    await asyncio.sleep(0.01)
    assert sent == ["TT=20.00\r", "PR=O\r"]
    await asyncio.sleep(0.07)  # no answer: sent again
    assert sent == ["TT=20.00\r", "PR=O\r", "PR=O\r"]
    assert commands.handle_response(Response("BV", error="bad value")).text == "PR=O"
    runner.cancel()


def test_frames_of_the_gateway_in_standalone_mode(app):
    feed(
        app,
        frame("R", MsgType.READ_DATA, 25),
        frame("B", MsgType.READ_ACK, 25, f88(48)),
        frame("R", MsgType.READ_DATA, 26),
    )
    assert ("otgw/boiler_water_temperature", "48.0") in drain(app)
