"""Application wiring: gateway messages to MQTT, MQTT commands to the gateway, Home Assistant discovery."""

import asyncio
import json
import logging
import secrets
import time
from collections.abc import Callable
from typing import Any

import aiomqtt

from otgw.config import Config
from otgw.discovery import discovery_messages, object_id_from_topic
from otgw.gateway import Commands, describe, open_gateway
from otgw.protocol import (
    DHW_MODES,
    KNOWN_IDS,
    VALUE_NAMES,
    VERSION,
    Response,
    Transaction,
    Transactions,
    decode,
    parse_frame,
    parse_override,
    parse_response,
)

log = logging.getLogger("otgw")

RESTORE_TIMEOUT = 5.0
RECONNECT_DELAY = 5.0
QUEUE_SIZE = 10000
OVERRIDE_QUERY_INTERVAL = 10.0  # at most this often when the remote override setpoint suggests a change

# Topics the bridge derives itself, next to the decoded values.
STATE_NAMES = ("target_temperature", "override_mode", "dhw_mode")

TRUE_VALUES = ("1", "on", "true", "yes")
FALSE_VALUES = ("0", "off", "false", "no")


def format_value(value: float | int) -> str:
    return str(value)


def _float(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


class App:
    def __init__(self, config: Config, clock: Callable[[], float] = time.monotonic):
        self.config = config
        self.clock = clock
        self.prefix = config.mqtt.prefix
        self.queue: asyncio.Queue[tuple[str, str, bool]] = asyncio.Queue(QUEUE_SIZE)
        self.commands = Commands()
        self.transactions = Transactions()

        self.values: dict[str, float | int] = {}
        self.override_mode: str | None = None  # off, temporary, constant; None until the gateway told us
        self.override_setpoint: float | None = None
        self.dhw_mode: str | None = None  # auto, on, off
        self.version: str | None = None
        self.available: bool | None = None
        self._published: dict[str, tuple[str, float]] = {}
        self._last_override_query = -OVERRIDE_QUERY_INTERVAL
        self._unknown_ids: set[int] = set()
        self._stale: set[str] = set()
        self._restored = False

    # --- publishing ------------------------------------------------------------------------

    def publish(self, topic: str, payload: str, retain: bool = True) -> None:
        try:
            self.queue.put_nowait((topic, payload, retain))
        except asyncio.QueueFull:
            log.warning("MQTT queue full, dropping %s", topic)

    def publish_value(self, name: str, payload: str, force: bool = False) -> None:
        """Publish a value when it changed, or when it was last published `refresh` seconds ago."""
        now = self.clock()
        last = self._published.get(name)
        refresh = self.config.publish.refresh
        if not force and last is not None and last[0] == payload and now - last[1] < refresh:
            return
        self._published[name] = (payload, now)
        self.publish(f"{self.prefix}/{name}", payload)

    def _set_available(self, available: bool) -> None:
        if self.available == available:
            return
        self.available = available
        if available:
            log.info("gateway: receiving data")
        else:
            log.warning("gateway: offline")
        self.publish(f"{self.prefix}/availability", "online" if available else "offline")

    # --- gateway lines ---------------------------------------------------------------------

    def handle_line(self, line: str) -> None:
        frame = parse_frame(line)
        if frame is not None:
            self._set_available(True)
            for transaction in self.transactions.feed(frame):
                self._handle_transaction(transaction)
            return
        response = parse_response(line)
        if response is not None:
            self._handle_response(response, line)
            return
        if match := VERSION.search(line):
            log.warning("gateway (re)started: %s; overrides were reset", line)
            self._set_version(match[1])
            self.query_status()
        elif line:
            log.debug("gateway: %s", line)

    def _handle_transaction(self, transaction: Transaction) -> None:
        data = transaction.data()
        if data is None:
            return
        data_id, value = data
        if data_id not in KNOWN_IDS:
            if data_id not in self._unknown_ids:
                self._unknown_ids.add(data_id)
                log.debug("not published: data id %d (value 0x%04X)", data_id, value)
            return
        for name, decoded in decode(data_id, value):
            self.values[name] = decoded
            self.publish_value(name, format_value(decoded))
            if name == "room_setpoint":
                self._publish_target()
            elif name == "remote_override_setpoint":
                self._check_override(decoded)

    def _handle_response(self, response: Response, line: str) -> None:
        command = self.commands.handle_response(response)
        if command is not None and command.raw:
            self.publish(f"{self.prefix}/command/response", line, retain=False)
        if response.error is not None:
            log.warning("gateway rejected %s: %s", command.text if command else "a command", response.error)
            return
        log.debug("gateway: %s", line)
        code, value = response.code, response.value
        if code in ("TT", "TC"):
            setpoint = _float(value)
            if setpoint is not None:
                if setpoint == 0:
                    self._set_override("off", None)
                else:
                    self._set_override("temporary" if code == "TT" else "constant", setpoint)
        elif code == "HW" and value in DHW_MODES:
            self._set_dhw_mode(DHW_MODES[value])
        elif code == "PR":
            if value.startswith("O="):
                override = parse_override(value)
                if override is None:
                    log.warning("unexpected override report %r", value)
                else:
                    self._set_override(*override)
            elif value.startswith("W=") and value[2:] in DHW_MODES:
                self._set_dhw_mode(DHW_MODES[value[2:]])
            elif value.startswith("A=") and (match := VERSION.search(value)):
                self._set_version(match[1])

    def _set_version(self, version: str) -> None:
        if version != self.version:
            self.version = version
            log.info("gateway firmware %s", version)
            self.publish_discovery()

    # --- override and hot water state -------------------------------------------------------

    def _set_override(self, mode: str, setpoint: float | None) -> None:
        if (mode, setpoint) != (self.override_mode, self.override_setpoint):
            log.info("override: %s%s", mode, f" {setpoint}" if setpoint is not None else "")
        self.override_mode = mode
        self.override_setpoint = setpoint
        self.publish_value("override_mode", mode)
        self._publish_target()

    def target_temperature(self) -> float | None:
        """The setpoint the thermostat follows: the override if one is active, else its own."""
        if self.override_mode in ("temporary", "constant") and self.override_setpoint is not None:
            return self.override_setpoint
        room_setpoint = self.values.get("room_setpoint")
        return float(room_setpoint) if room_setpoint is not None else None

    def _publish_target(self, force: bool = False) -> None:
        target = self.target_temperature()
        if target is not None:
            self.publish_value("target_temperature", format_value(target), force)

    def _check_override(self, remote_setpoint: float) -> None:
        """The thermostat reads the remote override setpoint: ask the gateway when it disagrees with us.

        A temporary override ends at the thermostat's next program change, without a message.
        """
        active = self.override_mode in ("temporary", "constant")
        if self.override_mode is not None and active == (remote_setpoint != 0):
            return
        now = self.clock()
        if now - self._last_override_query >= OVERRIDE_QUERY_INTERVAL:
            self._last_override_query = now
            self.commands.submit("PR=O")

    def _set_dhw_mode(self, mode: str) -> None:
        if mode != self.dhw_mode:
            log.info("hot water: %s", mode)
        self.dhw_mode = mode
        self.publish_value("dhw_mode", mode)

    def query_status(self) -> None:
        self.commands.submit("PR=O")
        self.commands.submit("PR=W")

    # --- MQTT messages ---------------------------------------------------------------------

    def subscriptions(self) -> list[str]:
        topics = [f"{self.prefix}/+/set", f"{self.prefix}/command", f"{self.prefix}/bridge/republish"]
        if self.config.discovery.enabled:
            topics.append(f"{self.config.discovery.prefix}/status")
        return topics

    def handle_message(self, topic: str, payload: bytes, retain: bool = False) -> None:
        if topic == f"{self.config.discovery.prefix}/status":
            if payload.strip() == b"online":
                log.info("Home Assistant came online, republishing")
                self.publish_all()
            return
        if not topic.startswith(self.prefix + "/"):
            return
        if retain:
            log.warning("ignoring retained command on %s", topic)
            return
        text = payload.decode("utf-8", "replace").strip()
        name = topic[len(self.prefix) + 1 :]
        if name == "bridge/republish":
            self.republish()
        elif name == "command":
            if text:
                log.info("raw command %s", text)
                self.commands.submit(text, raw=True)
        elif name.endswith("/set"):
            command = self.command_for(name.removesuffix("/set"), text)
            if command is not None:
                log.info("%s: %r -> %s", name, text, command)
                self.commands.submit(command)

    def command_for(self, name: str, text: str) -> str | None:
        """The gateway command for a value written to <prefix>/<name>/set."""
        lowered = text.lower()
        number = _float(text)
        if name == "target_temperature":
            if number is None or not 0 <= number <= 30:
                return self._invalid(name, text, "a temperature from 0 to 30 (0 cancels the override)")
            return f"{'TC' if self.override_mode == 'constant' else 'TT'}={number:.2f}"
        if name == "override_mode":
            if lowered in ("off", "none"):
                return "TT=0"
            if lowered in ("temporary", "constant"):
                target = self.target_temperature()
                if target is None:
                    log.warning("%s: no target temperature known yet", name)
                    return None
                return f"{'TT' if lowered == 'temporary' else 'TC'}={target:.2f}"
            return self._invalid(name, text, "off, temporary or constant")
        if name == "dhw_mode":
            commands = {"auto": "HW=T", "on": "HW=1", "off": "HW=0", "push": "HW=P"}
            return commands.get(lowered) or self._invalid(name, text, "auto, on, off or push")
        if name in ("dhw_setpoint", "max_ch_water_setpoint", "control_setpoint"):
            if number is None:
                return self._invalid(name, text, "a temperature (0 returns control to the thermostat)")
            code = {"dhw_setpoint": "SW", "max_ch_water_setpoint": "SH", "control_setpoint": "CS"}[name]
            return f"{code}={number:.2f}"
        if name == "outside_temperature":
            return f"OT={number:.2f}" if number is not None else "OT=-"  # non-numeric clears it
        if name == "max_relative_modulation_level":
            if number is None:
                return "MM=T"  # non-numeric: back to the thermostat
            if not 0 <= number <= 100:
                return self._invalid(name, text, "0 to 100")
            return f"MM={number:.0f}"
        if name == "ch_enabled":
            if lowered in TRUE_VALUES:
                return "CH=1"
            if lowered in FALSE_VALUES:
                return "CH=0"
            return self._invalid(name, text, "1 or 0")
        log.warning("no command for %s/%s/set", self.prefix, name)
        return None

    def _invalid(self, name: str, text: str, expected: str) -> None:
        log.warning("%s: invalid value %r, expected %s", name, text, expected)
        return None

    # --- discovery and retained state ---------------------------------------------------------

    def discovery(self) -> dict[str, dict[str, Any]]:
        return discovery_messages(self.config, self.version)

    def publish_discovery(self) -> None:
        if self.config.discovery.enabled:
            for topic, payload in self.discovery().items():
                self.publish(topic, json.dumps(payload, ensure_ascii=False))

    def expected_topics(self) -> set[str]:
        names = (*VALUE_NAMES, *STATE_NAMES, "status", "availability")
        return {f"{self.prefix}/{name}" for name in names}

    def apply_retained(self, topic: str, payload: bytes) -> None:
        """Remember retained topics that are no longer published (restore phase)."""
        if not payload:
            return
        if topic == self.prefix or topic.startswith(self.prefix + "/"):
            if topic not in self.expected_topics():
                self._stale.add(topic)
            return
        found = object_id_from_topic(self.config.discovery.prefix, topic)
        if found is None or found == self.config.discovery.object_id:
            return
        try:
            origin = json.loads(payload).get("origin", {}).get("name")
        except (ValueError, AttributeError):
            return
        if origin == "otgw":  # ours, with an old object_id
            self._stale.add(topic)

    def publish_all(self) -> None:
        """Discovery, removal of stale retained topics, bridge state, then online."""
        self.publish_discovery()
        for topic in sorted(self._stale):
            log.info("clearing stale retained topic %s", topic)
            self.publish(topic, "")
        self._stale.clear()
        if self.available is not None:
            self.publish(f"{self.prefix}/availability", "online" if self.available else "offline")
        if self.override_mode is not None:
            self.publish_value("override_mode", self.override_mode, force=True)
        if self.dhw_mode is not None:
            self.publish_value("dhw_mode", self.dhw_mode, force=True)
        self._publish_target(force=True)
        self.publish(f"{self.prefix}/status", "online")

    def republish(self) -> None:
        """Everything, including every value as soon as it is received again."""
        log.info("republishing")
        self._published.clear()
        self.publish_all()

    # --- running -----------------------------------------------------------------------------

    async def run(self) -> None:
        async with asyncio.TaskGroup() as group:
            group.create_task(self._mqtt(), name="mqtt")
            group.create_task(self.gateway(), name="gateway")
            group.create_task(self._poll(), name="poll")

    async def gateway(self) -> None:
        """Connect to the gateway, reconnecting whenever the connection fails or goes quiet."""
        config = self.config.gateway
        while True:
            try:
                reader, writer = await open_gateway(config)
            except OSError as error:
                log.warning("gateway %s: %s; retrying in %.0fs", describe(config), error, RECONNECT_DELAY)
                await asyncio.sleep(RECONNECT_DELAY)
                continue
            log.info("gateway: connected to %s", describe(config))
            try:
                await self._gateway_session(reader, writer)
            except OSError as error:
                log.warning("gateway: %s", error)
            finally:
                writer.close()
                self._set_available(False)
            await asyncio.sleep(RECONNECT_DELAY)

    async def _gateway_session(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        dropped = self.commands.clear()
        if dropped:
            log.warning("gateway: dropped %d commands queued while disconnected", dropped)
        self.transactions = Transactions()
        self.commands.submit("PR=A")
        self.query_status()

        async def write(data: str) -> None:
            writer.write(data.encode("ascii", "replace"))
            await writer.drain()

        timeout = self.config.gateway.timeout
        sender = asyncio.create_task(self.commands.run(write), name="gateway commands")
        try:
            while True:
                try:
                    async with asyncio.timeout(timeout):
                        raw = await reader.readline()
                except TimeoutError:
                    log.warning("gateway: nothing received for %ds, reconnecting", timeout)
                    return
                if not raw:
                    raise ConnectionError("connection closed")
                try:
                    self.handle_line(raw.decode("ascii", "replace").strip())
                except Exception:
                    log.exception("error handling gateway line %r", raw)
        finally:
            sender.cancel()

    async def _poll(self) -> None:
        """Ask for the override and hot water state now and then: the gateway does not report changes."""
        while True:
            await asyncio.sleep(self.config.gateway.poll_interval)
            if self.available:
                self.query_status()

    def _client(self) -> aiomqtt.Client:
        mqtt = self.config.mqtt
        return aiomqtt.Client(
            mqtt.host,
            mqtt.port,
            username=mqtt.username,
            password=mqtt.resolved_password(),
            identifier=mqtt.client_id,
            keepalive=mqtt.keepalive,
            will=aiomqtt.Will(f"{self.prefix}/status", "offline", qos=1, retain=True),
        )

    async def _mqtt(self) -> None:
        while True:
            try:
                async with self._client() as client:
                    log.info("connected to %s:%s", self.config.mqtt.host, self.config.mqtt.port)
                    await self._session(client)
            except aiomqtt.MqttError as error:
                log.warning("MQTT: %s; reconnecting in %.0fs", error, RECONNECT_DELAY)
                await asyncio.sleep(RECONNECT_DELAY)

    async def _session(self, client: aiomqtt.Client) -> None:
        if not self._restored:
            await self._restore(client)
            self._restored = True
        for topic in self.subscriptions():
            await client.subscribe(topic, self.config.mqtt.qos)
        self.publish_all()

        writer = asyncio.create_task(self._writer(client), name="mqtt writer")
        try:
            async for message in client.messages:
                try:
                    self.handle_message(message.topic.value, message.payload, message.retain)
                except Exception:
                    log.exception("error handling message on %s", message.topic.value)
        except asyncio.CancelledError:
            writer.cancel()
            await self._shutdown(client)
            raise
        finally:
            writer.cancel()

    async def _restore(self, client: aiomqtt.Client) -> None:
        """Collect our retained topics, to clear the ones that are no longer used."""
        qos = self.config.mqtt.qos
        sync = f"{self.prefix}/_sync/{secrets.token_hex(4)}"
        topics = [f"{self.prefix}/#"]
        if self.config.discovery.enabled:
            topics.append(f"{self.config.discovery.prefix}/device/+/config")
        for topic in topics:
            await client.subscribe(topic, qos)
        # Retained messages are delivered before this message comes back to us.
        await client.publish(sync, "sync", qos=qos)
        try:
            async with asyncio.timeout(RESTORE_TIMEOUT):
                async for message in client.messages:
                    if message.topic.value == sync:
                        break
                    if message.retain:
                        self.apply_retained(message.topic.value, message.payload)
        except TimeoutError:
            log.warning("restore: no sync message within %.0fs, continuing", RESTORE_TIMEOUT)
        for topic in topics:
            await client.unsubscribe(topic)

    async def _writer(self, client: aiomqtt.Client) -> None:
        qos = self.config.mqtt.qos
        while True:
            topic, payload, retain = await self.queue.get()
            await client.publish(topic, payload, qos=qos, retain=retain)

    async def _shutdown(self, client: aiomqtt.Client) -> None:
        log.info("shutting down")
        try:
            async with asyncio.timeout(5):
                while not self.queue.empty():
                    topic, payload, retain = self.queue.get_nowait()
                    await client.publish(topic, payload, qos=self.config.mqtt.qos, retain=retain)
                await client.publish(f"{self.prefix}/status", "offline", qos=1, retain=True)
        except Exception as error:  # best effort
            log.warning("shutdown incomplete: %s", error)
