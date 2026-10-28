# otgw

otgw connects an [OpenTherm Gateway][otgw] (Schelte Bron's firmware, for example the NodoShop OTGW) to
MQTT. It publishes what thermostat and boiler tell each other, accepts commands (room temperature
override, hot water, setpoints), and announces everything to Home Assistant through MQTT discovery.

The gateway is connected over USB serial (default `/dev/ttyUSB0`) or over TCP (ser2net, otmonitor
relay, ...). Version 1 (JSON configuration, `otgw/value/...` topics) is on the `v1` branch (tag
`v1-final`).

[otgw]: https://otgw.tclcode.com/

## Origins

This repository started in 2018 as a fork of Marten Jacobs' [py-otgw-mqtt][upstream] (2017), which is
no longer maintained. Version 1 is that code with changes from several forks merged in: TCP support,
`changed_messages_only`, more data ids, connection fixes, and setpoint override tracking. Thanks to Marten
Jacobs, Sen Haerens, Cornelis Hoeflake, HellMar, ikkemaniac, jodur and Lennard Klein, whose commits are
in the history.

Version 2 is a complete rewrite in the style of the other bridges in this home automation setup
(asyncio, YAML configuration, Home Assistant discovery). None of the original code is left, but the
history is kept, and the original MIT license and copyright notice still apply. The repository is no
longer a GitHub fork.

[upstream]: https://github.com/martenjacobs/py-otgw-mqtt

## How it works

- **The boiler's view.** The gateway reports every OpenTherm frame: the thermostat's request (`T`), the
  request as the gateway passed it on (`R`), the boiler's answer (`B`) and the answer the gateway gave
  the thermostat instead (`A`). otgw combines the frames of each exchange. For values the thermostat
  writes (room temperature, room setpoint, control setpoint) it publishes what the boiler was sent. For
  values it reads, it publishes the gateway's answer if there was one (remote override setpoint, an
  outside temperature set with `OT=`), and otherwise the boiler's. With a control setpoint override
  (`CS=`) you therefore see the setpoint the boiler really got.
- **Every value has its own topic,** with a plain payload (`20.5`, `1`). Temperatures can be negative
  (v1 published -3 °C as 253).
- **Published on change, and refreshed.** The thermostat repeats every exchange every few seconds. A
  value is published when it changes, and an unchanged value again after `publish.refresh` seconds
  (default 300), so time series (VictoriaMetrics, Grafana) have no long gaps.
- **Everything is retained**, so a subscriber that connects gets the latest values immediately.
- **Override state comes from the gateway.** otgw asks the gateway for the override (`PR=O`) and hot
  water setting (`PR=W`) at startup, every `gateway.poll_interval` seconds, and whenever the thermostat
  reads a remote override setpoint that doesn't match. A temporary override ends at the thermostat's next
  program change without any message; this is how otgw notices. otgw doesn't change or reset an
  override when it starts or stops (v1 `betterOverride` sent `TT=0` on every MQTT reconnect).
- **Commands are sent one at a time.** The gateway answers each command. otgw waits for the answer
  (3 seconds, 3 attempts) before sending the next one.
- **A silent gateway is reported.** When nothing is received for `gateway.timeout` seconds (default 30),
  otgw publishes `offline` on `<prefix>/availability` and reconnects. Home Assistant then shows the
  entities as unavailable.

## MQTT topics

`<prefix>` is `mqtt.prefix` (default `otgw`).

| topic                                   | direction | retained | payload                                                  |
|-----------------------------------------|-----------|----------|----------------------------------------------------------|
| `<prefix>/status`                       | out       | yes      | `online` / `offline` (last will)                         |
| `<prefix>/availability`                 | out       | yes      | `online` / `offline`: data received from the gateway     |
| `<prefix>/<value>`                      | out       | yes      | see the table below                                      |
| `<prefix>/override_mode`                | out       | yes      | `off` / `temporary` / `constant`                         |
| `<prefix>/target_temperature`           | out       | yes      | the setpoint the thermostat follows: the override if one is active, else `room_setpoint` |
| `<prefix>/dhw_mode`                     | out       | yes      | `auto` (thermostat) / `on` / `off`                       |
| `<prefix>/<name>/set`                   | in        | no       | see commands below                                       |
| `<prefix>/command`                      | in        | no       | any gateway command, e.g. `PR=M`                         |
| `<prefix>/command/response`             | out       | no       | the gateway's answer to a `command`, e.g. `PR: M=G`      |
| `<prefix>/bridge/republish`             | in        | no       | republish discovery, state and every value               |
| `homeassistant/device/otgw/config`      | out       | yes      | Home Assistant device discovery                          |

Values (OpenTherm data id in brackets). Flags are `1` or `0`.

| group           | values                                                                                                   |
|-----------------|----------------------------------------------------------------------------------------------------------|
| status (0)      | thermostat: `ch_enabled`, `dhw_enabled`, `cooling_enabled`, `otc_active`, `ch2_enabled`; boiler: `fault`, `ch_active`, `dhw_active`, `flame_on`, `cooling_active`, `ch2_active`, `diagnostic_indication` |
| faults (5)      | `service_request`, `lockout_reset`, `low_water_pressure`, `gas_flame_fault`, `air_pressure_fault`, `water_over_temperature`, `oem_fault_code` |
| room            | `room_temperature` (24), `room_setpoint` (16), `remote_override_setpoint` (9), `room_setpoint_2` (23)     |
| heating         | `control_setpoint` (1), `boiler_water_temperature` (25), `return_water_temperature` (28), `max_ch_water_setpoint` (57), `control_setpoint_2` (8), `boiler_water_temperature_2` (31) |
| modulation      | `relative_modulation_level` (17), `max_relative_modulation_level` (14)                                   |
| hot water       | `dhw_temperature` (26), `dhw_setpoint` (56), `dhw_flow_rate` (19)                                         |
| other           | `outside_temperature` (27), `ch_water_pressure` (18), `exhaust_temperature` (33), `oem_diagnostic_code` (115) |
| counters        | `burner_starts`, `ch_pump_starts`, `dhw_pump_starts`, `dhw_burner_starts`, `burner_operation_hours`, `ch_pump_operation_hours`, `dhw_pump_valve_operation_hours`, `dhw_burner_operation_hours` (116-123) |

A value is only published when thermostat and boiler exchange it. Many boilers don't support every
data id. Run with `--log-level DEBUG` to see which other data ids pass by.

Commands, on `<prefix>/<name>/set`:

| name                            | payload                                   | gateway command                                    |
|---------------------------------|-------------------------------------------|----------------------------------------------------|
| `target_temperature`            | `21.5`; `0` cancels                       | `TT` (temporary), or `TC` while a constant override is active |
| `override_mode`                 | `off` (or `none`), `temporary`, `constant` | `TT=0`, or `TT`/`TC` with the current target temperature |
| `dhw_mode`                      | `auto`, `on`, `off`, `push` (heat once)   | `HW=T`, `HW=1`, `HW=0`, `HW=P`                     |
| `dhw_setpoint`                  | `55`; `0` back to the thermostat          | `SW`                                               |
| `max_ch_water_setpoint`         | `70`; `0` back to the thermostat          | `SH`                                               |
| `outside_temperature`           | `-2.5`; empty clears                      | `OT`                                               |
| `max_relative_modulation_level` | `0`-`100`; anything else clears           | `MM`                                               |
| `control_setpoint`              | `45`; `0` back to the thermostat          | `CS` (must be repeated every minute; see the firmware documentation) |
| `ch_enabled`                    | `1` / `0` (only while `CS` is active)     | `CH`                                               |

Retained commands are ignored, so a stray retained message can't fire on every restart.

In Home Assistant the gateway is one device:

- a **thermostat** (climate). Setting the temperature sends a temporary override, which ends at the
  thermostat's next program change. Preset *Permanent* makes it constant. Preset *none* cancels the
  override. The mode shows whether the thermostat has central heating enabled, and the action whether the
  boiler is heating;
- **sensors and binary sensors** for the values above. The second heating circuit, cooling and rarely
  supported values are disabled by default;
- **configuration entities:** hot water (*Thermostaat* / *Aan* / *Uit*), the hot water setpoint and the
  maximum flow temperature;
- **diagnostics:** gateway connection, and a button to republish everything.

Entities are available when both `<prefix>/status` and `<prefix>/availability` are `online`.

At startup otgw clears every retained topic under `<prefix>/` that it no longer publishes. This removes
what version 1 left behind (`otgw/value/...`, retained `otgw/set/...` commands). otgw republishes
discovery when Home Assistant restarts (`homeassistant/status` = `online`).

## Installation

otgw is a normal Python package (Python ≥ 3.12). Installing it into a virtual environment gives you an
`otgw` command at `.venv/bin/otgw`. Its dependencies are `aiomqtt`, `pyserial-asyncio-fast`,
`pydantic` and `PyYAML`.

The exact dependency versions that were tested are pinned in `uv.lock`. Option A uses that file
automatically. With option B you install from `pyproject.toml`, which only gives minimum versions, so
you may get newer releases.

### Option A: `uv venv` + `uv pip` (classic workflow)

```sh
cd otgw
uv venv                          # creates .venv/
uv pip install -e .              # installs otgw (editable) and its dependencies
uv pip install --group dev       # optional: pytest and ruff, for development
```

To use the tested versions from `uv.lock` with this workflow:

```sh
uv export --frozen --no-dev --no-emit-project -o requirements.txt
uv pip install -r requirements.txt
uv pip install --no-deps .
```

### Option B: `uv sync` (project workflow)

```sh
cd otgw
uv sync                          # add --no-dev on a production machine
```

The rest of this README writes plain `otgw ...`. Activate `.venv` first, or put `.venv/bin/` or
`uv run` in front.

If the system Python is older than 3.12 (Raspberry Pi OS Bullseye has 3.9), uv downloads a suitable
Python for the virtual environment by itself.

## Configuration

See [`otgw.example.yaml`](otgw.example.yaml) for every option. Without a configuration file every
default applies: serial on `/dev/ttyUSB0`, prefix `otgw`.

### MQTT connection: `.env`

The broker host, port, username and password are not in `otgw.yaml`. They come from environment
variables, normally kept in a `.env` file. `.env` is in `.gitignore`, so it is never committed.

```sh
cp .env.example .env
chmod 600 .env
$EDITOR .env                                 # fill in OTGW_MQTT_HOST, _USERNAME, _PASSWORD
```

- **Where it looks:** otgw reads `.env` from the current directory. Use `--env-file PATH` to read
  another file.
- **Precedence:** a variable that is already set in the environment wins over the same one in the file.
- **Which settings:** every `mqtt:` setting can be set as `OTGW_MQTT_<KEY>`, and that overrides
  `otgw.yaml`.
- **Format:** plain `KEY=value` lines, the same format systemd's `EnvironmentFile=` uses.

### Checking the gateway and the configuration

```sh
ls -l /dev/serial/by-id/                     # the stable name of the USB serial adapter
otgw -c otgw.yaml --monitor                  # print what the gateway reports for 60 seconds; or --monitor 300
otgw -c otgw.yaml --check-config             # validate the configuration
otgw -c otgw.yaml --dump-discovery           # print what Home Assistant will receive
```

`--monitor` doesn't need MQTT. It only reads (and asks for the override and hot water state). The
gateway accepts one serial connection, so stop the running service first.

## Running

```sh
otgw -c otgw.yaml                            # reads .env; add --log-level DEBUG to see every gateway line
```

Stop it with Ctrl+C. otgw publishes `offline` on `<prefix>/status` before exiting.

### As a systemd service

The unit file [`deploy/otgw.service`](deploy/otgw.service) expects:

| what                         | where                      |
|------------------------------|----------------------------|
| the code and its `.venv/`    | `/opt/otgw`                |
| the configuration            | `/etc/otgw/otgw.yaml`      |
| the MQTT settings (`.env`)   | `/etc/otgw/env`            |
| the service account          | user `otgw`, also in group `dialout` |

The `dialout` group gives access to `/dev/ttyUSB*`. Every step:

```sh
# 1. service account and code
sudo useradd --system --home-dir /opt/otgw --shell /usr/sbin/nologin --groups dialout otgw
sudo git clone <repo-url> /opt/otgw
sudo chown -R otgw: /opt/otgw

# 2. virtual environment, as the otgw user (option A shown; option B: uv sync --frozen --no-dev)
cd /opt/otgw
sudo -u otgw uv venv
sudo -u otgw uv export --frozen --no-dev --no-emit-project -o requirements.txt
sudo -u otgw uv pip install -r requirements.txt
sudo -u otgw uv pip install --no-deps .
sudo -u otgw /opt/otgw/.venv/bin/otgw --monitor 30   # check: serial access and data

# 3. configuration and MQTT settings
sudo install -d /etc/otgw
sudo cp otgw.example.yaml /etc/otgw/otgw.yaml
sudoedit /etc/otgw/otgw.yaml                       # gateway.device: /dev/serial/by-id/...
sudo install -m 600 .env.example /etc/otgw/env
sudoedit /etc/otgw/env                             # fill in host, username, password
sudo /opt/otgw/.venv/bin/otgw -c /etc/otgw/otgw.yaml --env-file /etc/otgw/env --check-config

# 4. service
sudo cp deploy/otgw.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now otgw
journalctl -u otgw -f                              # follow the log
```

`uv` must be usable by the `otgw` user. If it's installed in your home directory (`~/.local/bin/uv`),
call it by full path or install it system-wide.

To update:

```sh
cd /opt/otgw && sudo -u otgw git pull
sudo -u otgw uv pip install --no-deps .            # option B: sudo -u otgw uv sync --frozen --no-dev
sudo systemctl restart otgw
```

If `uv.lock` changed, first rerun the `uv export` and `uv pip install -r requirements.txt` lines from
step 2.

## Migrating from v1

v1 and v2 can't run side by side: the gateway accepts only one serial connection.

1. Stop and disable v1 (`sudo systemctl disable --now otgw`; v1 ran as root from `/opt/otgw`). Move
   its directory aside before cloning v2 there.
2. Check v2 on the gateway: `otgw --monitor`.
3. Start v2. It clears the retained v1 topics under `otgw/`.
4. Update everything that used the v1 topics:

   | v1                                         | v2                                      |
   |--------------------------------------------|-----------------------------------------|
   | `otgw/value` (online/offline)              | `otgw/status`                           |
   | `otgw/value/<name>`                        | `otgw/<name>` (same names)              |
   | `otgw/value/status/<flag>`                 | `otgw/<flag>`                           |
   | `otgw/value/master_slave_status`           | the separate flags                      |
   | `otgw/value/override_mode`                 | `otgw/override_mode` (now from the gateway itself) |
   | `otgw/set/room_setpoint/temporary`         | `otgw/target_temperature/set` (temporary unless a constant override is active) |
   | `otgw/set/room_setpoint/constant`          | `otgw/override_mode/set` `constant`, then `otgw/target_temperature/set` |
   | `otgw/set/hot_water/enable`                | `otgw/dhw_mode/set` (`on`/`off`/`auto`) |
   | `otgw/set/hot_water/temperature`           | `otgw/dhw_setpoint/set`                 |
   | `otgw/set/central_heating/temperature`     | `otgw/max_ch_water_setpoint/set`        |
   | `otgw/set/central_heating/enable`          | `otgw/ch_enabled/set`                   |
   | `otgw/set/outside_temperature`             | `otgw/outside_temperature/set`          |
   | `otgw/set/control_setpoint`                | `otgw/control_setpoint/set`             |
   | `otgw/set/max_modulation`                  | `otgw/max_relative_modulation_level/set` |
   | `otgw/set/cmd`                             | `otgw/command` (answer on `otgw/command/response`) |
   | `otgw/value/unknown/...`                   | not published; `--log-level DEBUG` shows them |

Other differences subscribers may notice: values are retained and only published on change (plus the
refresh). v1 published negative temperatures wrongly (−3 °C as 253).

## Development

Install with the dev tools (`uv pip install -e . && uv pip install --group dev`, or `uv sync`). Then:

```sh
pytest                                   # or: uv run pytest
ruff check . && ruff format --check .
```

Dependencies are declared in `pyproject.toml`. Add one with `uv add <package>` (or `uv add --dev
<package>`), which also updates `uv.lock`. If you only edit `pyproject.toml` by hand, run `uv lock`
afterwards.

The frame decoding and transactions (`protocol.py`) and the application logic (`app.py`) are tested
without a gateway or MQTT, with gateway lines built in the tests and a fake clock.
