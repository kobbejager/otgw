import pytest
from pydantic import ValidationError

from otgw.config import parse_config


def test_defaults():
    config = parse_config("", env={})
    assert config.mqtt.prefix == "otgw"
    assert config.gateway.device == "/dev/ttyUSB0"
    assert config.thermostat.temperature.max == 30


def test_tcp_gateway():
    config = parse_config("gateway: {host: otgw.local, port: 25238}", env={})
    assert (config.gateway.host, config.gateway.port, config.gateway.device) == ("otgw.local", 25238, None)
    with pytest.raises(ValidationError, match="port is required"):
        parse_config("gateway: {host: otgw.local}", env={})


def test_env_overrides_mqtt():
    config = parse_config("mqtt: {host: a}", env={"OTGW_MQTT_HOST": "b", "OTGW_MQTT_PORT": "1884"})
    assert (config.mqtt.host, config.mqtt.port) == ("b", 1884)


def test_validation():
    with pytest.raises(ValidationError):
        parse_config("gateway: {devise: /dev/ttyUSB0}", env={})
    with pytest.raises(ValidationError, match="must be below"):
        parse_config("limits: {dhw_setpoint: {min: 70, max: 60}}", env={})
