# Python OTGW MQTT bridge

This package allows for communication between an OpenTherm Gateway, running the [firmware by Schelte Bron](http://otgw.tclcode.com/) and an MQTT service. It was tested using [Home Assistant](http://www.home-assistant.io)'s built-in MQTT broker.

## Supported OTGW gateway communication protocols
Currently, only direct serial and TCP communication are supported, but implementing further types is pretty easy. I'm open to pull requests.
> NOTE: TCP connections are, as yet, untested. Please open an issue if you're experiencing difficulties.

## Supported MQTT brokers
The MQTT client used is [paho](https://www.eclipse.org/paho/). It's one of the most widely-used MQTT clients for Python, so it should work on most brokers. If you're having problems with a certain type, please open an issue or send me a pull request with a fix.

## Configuration
The configuration for the bridge is located in config.json.

### Example configuration
To use the serial connection to the OTGW, use a config.json like the following:
```json
{
    "otgw" : {
        "type": "serial",
        "device": "/dev/ttyUSB0",
        "baudrate": 9600
    },
    "mqtt" : {
        "client_id": "otgw",
        "host": "127.0.0.1",
        "port": 1883,
        "keepalive": 60,
        "bind_address": "",
        "username": null,
        "password": null,
        "qos": 0,
        "pub_topic_namespace": "otgw/value",
        "sub_topic_namespace": "otgw/set"
    }
}
```

To use a TCP connection, replace the OTGW section with this:
```json
    "otgw" : {
        "type": "tcp",
        "host": "<OTGW HOSTNAME OR IP>",
        "port": 2323
    },
```

## Installation
To install this script as a daemon, run the following commands (on a Debian-based distribution):

1. Install dependencies:
   ```bash
   sudo apt install python3 python3-serial python3-paho-mqtt
   ```
2. Create a new folder, for example:
   ```bash
   sudo mkdir -p /opt/py-otgw-mqtt
   cd /opt/py-otgw-mqtt
   ```
3. Clone this repository into the current directory:
   ```bash
   sudo git clone https://github.com/HellMar/py-otgw-mqtt.git .
   ```
4. Copy the config.json.example file to config.json
   ```bash
   sudo cp config.json.example config.json
   ```
5. Change `config.json` with your favorite text editor
6. Copy the service file to the systemd directory. If you used a different folder name than `/usr/lib/py-otgw-mqtt` you will need to change the `WorkingDirectory` in the file first.
   ```bash
   sudo cp ./otgw.service /etc/systemd/system/
   ```
7. Enable the service so it starts up on boot:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable otgw.service
   ```
8. Start up the service
   ```bash
   sudo systemctl start otgw.service
   ```
9. View the log to see if everything works
   ```bash
   journalctl -u otgw.service -f
   ```

## Topics

### Publish topics
By default, the service publishes messages to the following MQTT topics:

- otgw/value => _The status of the service_
- otgw/value/master_slave_status
- otgw/value/ch_enabled
- otgw/value/dhw_enabled
- otgw/value/cooling_enabled
- otgw/value/control_setpoint
- otgw/value/remote_override_setpoint
- otgw/value/max_relative_modulation_level
- otgw/value/room_setpoint
- otgw/value/relative_modulation_level
- otgw/value/ch_water_pressure
- otgw/value/room_temperature
- otgw/value/boiler_water_temperature
- otgw/value/dhw_temperature
- otgw/value/outside_temperature
- otgw/value/return_water_temperature
- otgw/value/dhw_setpoint
- otgw/value/max_ch_water_setpoint
- otgw/value/burner_starts
- otgw/value/ch_pump_starts
- otgw/value/dhw_pump_starts
- otgw/value/dhw_burner_starts
- otgw/value/burner_operation_hours
- otgw/value/ch_pump_operation_hours
- otgw/value/dhw_pump_valve_operation_hours
- otgw/value/dhw_burner_operation_hours

> If you've changed the pub_topic_namespace value in the configuration, replace `otgw/value` with your configured value.
> __TODO:__ Add description of all topics

### Subscription topics
By default, the service listens to messages from the following MQTT topics:

- otgw/set/room_setpoint/temporary - TT - Float
- otgw/set/room_setpoint/constant - TC - Float
- otgw/set/outside_temperature - OT - Float
- otgw/set/hot_water/enable - HW - Boolean
- otgw/set/hot_water/temperature - SW - Float
- otgw/set/central_heating/enable - CH - Boolean
- otgw/set/central_heating/temperature - SH - Float
- otgw/set/control_setpoint - CS - Float
- otgw/set/max_modulation - MM - Integer 0-100

> __TODO:__ Add description of all topics
