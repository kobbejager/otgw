"""Home Assistant MQTT device-based discovery (``<prefix>/device/<object_id>/config``)."""

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from otgw.config import Config

try:
    VERSION = version("otgw")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    VERSION = "dev"

ORIGIN = {"name": "otgw", "sw_version": VERSION}

# Home Assistant labels, presets and options. The MQTT payloads stay English.
PRESETS = {"temporary": "Tijdelijk", "constant": "Permanent"}
DHW_OPTIONS = {"auto": "Thermostaat", "on": "Aan", "off": "Uit"}

_TEMPERATURE = {
    "device_class": "temperature",
    "unit_of_measurement": "°C",
    "state_class": "measurement",
    "suggested_display_precision": 1,
}
_PERCENT = {"unit_of_measurement": "%", "state_class": "measurement", "suggested_display_precision": 0}
_COUNTER = {"state_class": "total_increasing", "entity_category": "diagnostic"}
_HOURS = {**_COUNTER, "device_class": "duration", "unit_of_measurement": "h"}
_DIAGNOSTIC = {"entity_category": "diagnostic"}
_DISABLED = {"enabled_by_default": False}

SENSORS: dict[str, tuple[str, dict[str, Any]]] = {
    "room_temperature": ("Kamertemperatuur", _TEMPERATURE),
    "room_setpoint": ("Ingestelde kamertemperatuur", _TEMPERATURE),
    "remote_override_setpoint": ("Override kamertemperatuur", {**_TEMPERATURE, **_DIAGNOSTIC}),
    "control_setpoint": ("Gevraagde aanvoertemperatuur", _TEMPERATURE),
    "boiler_water_temperature": ("Aanvoertemperatuur", _TEMPERATURE),
    "return_water_temperature": ("Retourtemperatuur", _TEMPERATURE),
    "dhw_temperature": ("Warmwatertemperatuur", _TEMPERATURE),
    "outside_temperature": ("Buitentemperatuur", _TEMPERATURE),
    "exhaust_temperature": ("Rookgastemperatuur", {**_TEMPERATURE, **_DISABLED}),
    "relative_modulation_level": ("Modulatie", _PERCENT),
    "max_relative_modulation_level": ("Maximale modulatie", {**_PERCENT, **_DIAGNOSTIC}),
    "ch_water_pressure": (
        "Waterdruk",
        {
            "device_class": "pressure",
            "unit_of_measurement": "bar",
            "state_class": "measurement",
            "suggested_display_precision": 1,
        },
    ),
    "dhw_flow_rate": (
        "Warmwaterdebiet",
        {
            "device_class": "volume_flow_rate",
            "unit_of_measurement": "L/min",
            "state_class": "measurement",
            **_DISABLED,
        },
    ),
    "control_setpoint_2": ("Gevraagde aanvoertemperatuur 2", {**_TEMPERATURE, **_DISABLED}),
    "room_setpoint_2": ("Ingestelde kamertemperatuur 2", {**_TEMPERATURE, **_DISABLED}),
    "boiler_water_temperature_2": ("Aanvoertemperatuur 2", {**_TEMPERATURE, **_DISABLED}),
    "burner_starts": ("Branderstarts", _COUNTER),
    "ch_pump_starts": ("Starts cv-pomp", _COUNTER),
    "dhw_pump_starts": ("Starts warmwaterpomp", _COUNTER),
    "dhw_burner_starts": ("Branderstarts warm water", _COUNTER),
    "burner_operation_hours": ("Branderuren", _HOURS),
    "ch_pump_operation_hours": ("Draaiuren cv-pomp", _HOURS),
    "dhw_pump_valve_operation_hours": ("Draaiuren warmwaterpomp", _HOURS),
    "dhw_burner_operation_hours": ("Branderuren warm water", _HOURS),
    "oem_fault_code": ("Storingscode", _DIAGNOSTIC),
    "oem_diagnostic_code": ("Diagnosecode", {**_DIAGNOSTIC, **_DISABLED}),
}

_PROBLEM = {"device_class": "problem"}
_RUNNING = {"device_class": "running"}

BINARY_SENSORS: dict[str, tuple[str, dict[str, Any]]] = {
    "flame_on": ("Vlam", {"icon": "mdi:fire"}),
    "ch_active": ("Verwarmen", _RUNNING),
    "dhw_active": ("Warm water", _RUNNING),
    "fault": ("Storing", _PROBLEM),
    "ch_enabled": ("Verwarming ingeschakeld", _DIAGNOSTIC),
    "dhw_enabled": ("Warm water ingeschakeld", _DIAGNOSTIC),
    "diagnostic_indication": ("Diagnosemelding", {**_PROBLEM, **_DIAGNOSTIC}),
    "service_request": ("Onderhoud nodig", {**_PROBLEM, **_DIAGNOSTIC}),
    "lockout_reset": ("Reset nodig", {**_PROBLEM, **_DIAGNOSTIC}),
    "low_water_pressure": ("Lage waterdruk", {**_PROBLEM, **_DIAGNOSTIC}),
    "gas_flame_fault": ("Gas- of vlamstoring", {**_PROBLEM, **_DIAGNOSTIC}),
    "air_pressure_fault": ("Luchtdrukstoring", {**_PROBLEM, **_DIAGNOSTIC}),
    "water_over_temperature": ("Oververhitting", {**_PROBLEM, **_DIAGNOSTIC}),
    "cooling_enabled": ("Koeling ingeschakeld", {**_DIAGNOSTIC, **_DISABLED}),
    "cooling_active": ("Koelen", {**_RUNNING, **_DISABLED}),
    "otc_active": ("Weersafhankelijke regeling", {**_DIAGNOSTIC, **_DISABLED}),
    "ch2_enabled": ("Verwarming 2 ingeschakeld", {**_DIAGNOSTIC, **_DISABLED}),
    "ch2_active": ("Verwarmen 2", {**_RUNNING, **_DISABLED}),
}


def config_topic(discovery_prefix: str, object_id: str) -> str:
    return f"{discovery_prefix}/device/{object_id}/config"


def object_id_from_topic(discovery_prefix: str, topic: str) -> str | None:
    parts = topic.split("/")
    base = discovery_prefix.split("/")
    if len(parts) == len(base) + 3 and parts[: len(base)] == base and parts[-1] == "config":
        return parts[len(base) + 1]
    return None


def _mapping_template(mapping: dict[str, str], default: str) -> str:
    pairs = ", ".join(f"'{key}': '{value}'" for key, value in mapping.items())
    return f"{{{{ {{{pairs}}}.get(value, '{default}') }}}}"


def _climate(config: Config, prefix: str) -> dict[str, Any]:
    temperature = config.thermostat.temperature
    reverse = {label: mode for mode, label in PRESETS.items()}
    return {
        "platform": "climate",
        "name": "Thermostaat",
        "current_temperature_topic": f"{prefix}/room_temperature",
        "temperature_state_topic": f"{prefix}/target_temperature",
        "temperature_command_topic": f"{prefix}/target_temperature/set",
        "modes": ["off", "heat"],
        "mode_state_topic": f"{prefix}/ch_enabled",
        "mode_state_template": "{{ 'heat' if value == '1' else 'off' }}",
        "action_topic": f"{prefix}/ch_active",
        "action_template": "{{ 'heating' if value == '1' else 'idle' }}",
        "preset_modes": list(PRESETS.values()),
        "preset_mode_state_topic": f"{prefix}/override_mode",
        "preset_mode_value_template": _mapping_template(PRESETS, "none"),
        "preset_mode_command_topic": f"{prefix}/override_mode/set",
        "preset_mode_command_template": _mapping_template(reverse, "off"),
        "min_temp": temperature.min,
        "max_temp": temperature.max,
        "temp_step": temperature.step,
        "precision": 0.1,
        "temperature_unit": "C",
    }


def _number(name: str, label: str, limits: Any, prefix: str) -> dict[str, Any]:
    return {
        "platform": "number",
        "name": label,
        "state_topic": f"{prefix}/{name}",
        "command_topic": f"{prefix}/{name}/set",
        "device_class": "temperature",
        "unit_of_measurement": "°C",
        "min": limits.min,
        "max": limits.max,
        "step": limits.step,
        "mode": "box",
        "entity_category": "config",
    }


def device_payload(config: Config, gateway_version: str | None = None) -> dict[str, Any]:
    prefix = config.mqtt.prefix
    discovery = config.discovery
    oid = discovery.object_id
    status = {"topic": f"{prefix}/status", "payload_available": "online", "payload_not_available": "offline"}
    gateway = {**status, "topic": f"{prefix}/availability"}

    info: dict[str, Any] = {
        "identifiers": [oid],
        "name": discovery.name,
        "manufacturer": "Schelte Bron",
        "model": "OpenTherm Gateway",
    }
    if gateway_version:
        info["sw_version"] = gateway_version
    if discovery.area:
        info["suggested_area"] = discovery.area

    components: dict[str, dict[str, Any]] = {"thermostat": _climate(config, prefix)}
    for name, (label, settings) in SENSORS.items():
        components[name] = {
            "platform": "sensor",
            "name": label,
            "state_topic": f"{prefix}/{name}",
            **settings,
        }
    for name, (label, settings) in BINARY_SENSORS.items():
        components[name] = {
            "platform": "binary_sensor",
            "name": label,
            "state_topic": f"{prefix}/{name}",
            "payload_on": "1",
            "payload_off": "0",
            **settings,
        }
    limits = config.limits
    components["dhw_setpoint"] = _number("dhw_setpoint", "Warmwater setpoint", limits.dhw_setpoint, prefix)
    components["max_ch_water_setpoint"] = _number(
        "max_ch_water_setpoint", "Maximale aanvoertemperatuur", limits.max_ch_water_setpoint, prefix
    )
    components["dhw_mode"] = {
        "platform": "select",
        "name": "Warm water",
        "state_topic": f"{prefix}/dhw_mode",
        "value_template": _mapping_template(DHW_OPTIONS, ""),
        "command_topic": f"{prefix}/dhw_mode/set",
        "command_template": _mapping_template({v: k for k, v in DHW_OPTIONS.items()}, "auto"),
        "options": list(DHW_OPTIONS.values()),
        "entity_category": "config",
    }
    # Bridge diagnostics: only depend on the bridge itself, not on data from the gateway.
    components["gateway"] = {
        "platform": "binary_sensor",
        "name": "Gateway verbonden",
        "device_class": "connectivity",
        "entity_category": "diagnostic",
        "state_topic": f"{prefix}/availability",
        "payload_on": "online",
        "payload_off": "offline",
        "availability": [status],
    }
    components["republish"] = {
        "platform": "button",
        "name": "Opnieuw publiceren",
        "entity_category": "diagnostic",
        "command_topic": f"{prefix}/bridge/republish",
        "payload_press": "PRESS",
        "availability": [status],
    }

    for key, component in components.items():
        component["unique_id"] = f"{oid}_{key}"
        if discovery.default_entity_ids:
            component["default_entity_id"] = f"{component['platform']}.{oid}_{key}".lower()

    return {
        "device": info,
        "origin": ORIGIN,
        "availability": [status, gateway],
        "availability_mode": "all",
        "qos": config.mqtt.qos,
        "components": components,
    }


def discovery_messages(config: Config, gateway_version: str | None = None) -> dict[str, dict[str, Any]]:
    """All discovery topics and payloads."""
    topic = config_topic(config.discovery.prefix, config.discovery.object_id)
    return {topic: device_payload(config, gateway_version)}
