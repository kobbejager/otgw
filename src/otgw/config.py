"""Configuration file (otgw.yaml) loading and validation."""

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MqttConfig(Model):
    host: str = "127.0.0.1"
    port: int = 1883
    username: str | None = None
    password: str | None = None
    password_file: Path | None = None
    prefix: str = "otgw"
    client_id: str = "otgw"
    keepalive: int = 60
    qos: Literal[0, 1, 2] = 1

    def resolved_password(self) -> str | None:
        if self.password_file is not None:
            return self.password_file.read_text().strip()
        return self.password


class GatewayConfig(Model):
    # Serial (USB) connection; /dev/serial/by-id/... survives re-plugging and reboots.
    device: str | None = "/dev/ttyUSB0"
    baudrate: int = 9600
    # TCP connection (ser2net, otmonitor relay, ...) instead of serial.
    host: str | None = None
    port: int | None = None
    timeout: int = Field(30, gt=0)  # seconds without a message before reconnecting and reporting offline
    poll_interval: int = Field(60, gt=0)  # seconds between override / hot water status queries

    @model_validator(mode="after")
    def _check(self) -> "GatewayConfig":
        if self.host is not None:
            if self.port is None:
                raise ValueError("gateway.port is required with gateway.host")
            self.device = None
        elif self.device is None:
            raise ValueError("set gateway.device (serial) or gateway.host and gateway.port (TCP)")
        return self


class PublishConfig(Model):
    # Values are published when they change, and an unchanged value again after `refresh` seconds,
    # so time series databases see every value at least that often.
    refresh: int = Field(300, gt=0)


class Range(Model):
    min: float
    max: float
    step: float = Field(1.0, gt=0)

    @model_validator(mode="after")
    def _check(self) -> "Range":
        if self.min >= self.max:
            raise ValueError(f"min ({self.min}) must be below max ({self.max})")
        return self


class ThermostatConfig(Model):
    # The gateway accepts overrides from 0 to 30 °C (0 cancels the override).
    temperature: Range = Range(min=5, max=30, step=0.5)


class LimitsConfig(Model):
    dhw_setpoint: Range = Range(min=40, max=65)
    max_ch_water_setpoint: Range = Range(min=30, max=90)


class DiscoveryConfig(Model):
    enabled: bool = True
    prefix: str = "homeassistant"
    default_entity_ids: bool = True  # needs Home Assistant >= 2025.10; disable on older versions
    object_id: str = Field("otgw", pattern=r"^[A-Za-z0-9_-]+$")  # base of the unique ids and entity ids
    name: str = "OpenTherm Gateway"
    area: str | None = None


class Config(Model):
    mqtt: MqttConfig = MqttConfig()
    gateway: GatewayConfig = GatewayConfig()
    publish: PublishConfig = PublishConfig()
    thermostat: ThermostatConfig = ThermostatConfig()
    limits: LimitsConfig = LimitsConfig()
    discovery: DiscoveryConfig = DiscoveryConfig()


class _Loader(yaml.SafeLoader):
    """SafeLoader with YAML 1.2 booleans (so `on:`/`off:` stay strings) and an `!env` tag."""


_Loader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Loader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF")
)


def _env(loader: yaml.SafeLoader, node: yaml.Node) -> str:
    name = loader.construct_scalar(node)
    if name not in os.environ:
        raise ValueError(f"environment variable {name} is not set")
    return os.environ[name]


_Loader.add_constructor("!env", _env)


ENV_PREFIX = "OTGW_MQTT_"


def mqtt_from_env(env: Mapping[str, str]) -> dict[str, str]:
    """MQTT settings from OTGW_MQTT_<FIELD> variables, e.g. OTGW_MQTT_HOST."""
    settings = {}
    for field in MqttConfig.model_fields:
        value = env.get(ENV_PREFIX + field.upper())
        if value not in (None, ""):
            settings[field] = value
    return settings


def parse_config(text: str, env: Mapping[str, str] | None = None) -> Config:
    """Parse otgw.yaml; OTGW_MQTT_* environment variables override its `mqtt:` section."""
    data = yaml.load(text, Loader=_Loader) or {}
    overrides = mqtt_from_env(os.environ if env is None else env)
    if overrides and isinstance(data, dict):
        data["mqtt"] = {**(data.get("mqtt") or {}), **overrides}
    return Config.model_validate(data)


def load_config(path: str | Path) -> Config:
    return parse_config(Path(path).read_text())


def load_env_file(path: str | Path) -> list[str]:
    """Load KEY=VALUE lines (systemd EnvironmentFile / .env syntax) into os.environ.

    Variables that are already set win, so the real environment overrides the file.
    Returns the names that were set.
    """
    loaded = []
    for number, raw in enumerate(Path(path).read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"{path}:{number}: expected KEY=VALUE")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
