import re
from threading import Thread
from time import sleep
import logging
import collections
import copy, json

log = logging.getLogger(__name__)

# Default namespace for the topics. Will be overwritten with the value in
# config
pub_topic_namespace="otgw/value"
sub_topic_namespace="otgw/set"
ha_publish_namespace="homeassistant"

# Parse hex string to int
def hex_int(hex):
    return int(hex, 16)

# Pre-compile a regex to parse valid OTGW-messages
line_parser = re.compile(
    r'^(?P<source>[BART])(?P<type>[0-9A-F])(?P<res>[0-9A-F])'
    r'(?P<id>[0-9A-F]{2})(?P<data>[0-9A-F]{4})$'
)


def flags_msg_generator(ot_id, val):
    r"""
    Generate the pub-messages from a boolean value.

    Currently, only flame status is supported. Any other items will be returned
    as-is.

    Returns a generator for the messages
    """
    yield ("{}/{}".format(pub_topic_namespace, ot_id), val, )
    if(ot_id == "master_slave_status"):

        ####
        # data is 2 byte
        # 0000 0000
        # |       |
        # master   slave
        ####

        for bit, bit_name in master_slave_status_bits.items():

            yield ("{}/{}".format(pub_topic_namespace, bit_name),
                   int(val & ( 1 << bit ) > 0), )


def float_msg_generator(ot_id, val):
    r"""
    Generate the pub-messages from a float-based value

    Returns a generator for the messages
    """
    yield ("{}/{}".format(pub_topic_namespace, ot_id), round(val/float(256), 2), )

def int_msg_generator(ot_id, val):
    r"""
    Generate the pub-messages from an integer-based value

    Returns a generator for the messages
    """
    yield ("{}/{}".format(pub_topic_namespace, ot_id), val, )

def other_msg_generator(source, ttype, res, did, data):
    r"""
    Generate the pub-messages from an unknown message.
    Casts value as string.

    Returns a generator for the messages
    """
    yield ("{}/{}/{}/{}/{}/{}".format(pub_topic_namespace, 'unknown', source, ttype, res, did), str(data), )

def get_messages(message):
    r"""
    Generate the pub-messages from the supplied OT-message

    Returns a generator for the messages
    """
    info = line_parser.match(message)
    if info is None:
        if message:
            log.error("Did not understand message: '{}'".format(message))
        return iter([])
    (source, ttype, res, did, data) = \
        map(lambda f, d: f(d),
            (str, lambda _: hex_int(_) & 7, hex_int, hex_int, hex_int),
            info.groups())

    if source not in ('B', 'T', 'A') \
        or ttype not in (1,4):
        return iter([])
    if did not in opentherm_ids:
        return other_msg_generator(source, ttype, res, did, data)

    id_name, parser = opentherm_ids[did]
    return parser(id_name, data)


# Map the opentherm ids (named group 'id' in the line parser regex) to
# discriptive names and message creators. I put this here because the
# referenced generators have to be assigned first
opentherm_ids = {
    # flame status is special case... multiple bits of data. see flags_msg_generator
	0:   ("master_slave_status",flags_msg_generator,),
	1:   ("control_setpoint/setpoint",float_msg_generator,),
	9:   ("remote_override_setpoint/setpoint",float_msg_generator,),
	14:  ("max_relative_modulation_level/level",float_msg_generator,),
	16:  ("room_setpoint/setpoint",float_msg_generator,),
	17:  ("relative_modulation_level/level",float_msg_generator,),
	18:  ("ch_water_pressure/pressure",float_msg_generator,),
	24:  ("room_temperature/temperature",float_msg_generator,),
	25:  ("boiler_water_temperature/temperature",float_msg_generator,),
	26:  ("dhw_temperature/temperature",float_msg_generator,),
	27:  ("outside_temperature/temperature",float_msg_generator,),
	28:  ("return_water_temperature/temperature",float_msg_generator,),
	56:  ("dhw_setpoint/setpoint",float_msg_generator,),
	57:  ("max_ch_water_setpoint/setpoint",float_msg_generator,),
	116: ("burner_starts/count",int_msg_generator,),
	117: ("ch_pump_starts/count",int_msg_generator,),
	118: ("dhw_pump_starts/count",int_msg_generator,),
	119: ("dhw_burner_starts/count",int_msg_generator,),
	120: ("burner_operation_hours/hours",int_msg_generator,),
	121: ("ch_pump_operation_hours/hours",int_msg_generator,),
	122: ("dhw_pump_valve_operation_hours/hours",int_msg_generator,),
	123: ("dhw_burner_operation_hours/hours",int_msg_generator,),
}

# { <bit>, <name>}
master_slave_status_bits = {
    0:  "fault/state",
    1:  "ch_active/state",
    2:  "dhw_active/state",
    3:  "flame_on/state",
    4:  "cooling_active/state",
    5:  "ch2_active/state",
    6:  "diagnostic_indication/state",
    7:  "bit_7/state",
    8:  "ch_enabled/state",
    9:  "dhw_enabled/state",
    10: "cooling_enabled/state",
    11: "otc_active/state",
    12: "ch2_enabled/state",
    13: "bit_13/state",
    14: "bit_14/state",
    15: "bit_15/state",
}


def cleanNullTerms(d):
    clean = {}
    for k, v in d.items():
        if isinstance(v, dict):
            nested = cleanNullTerms(v)
            if len(nested.keys()) > 0:
                clean[k] = nested
        elif v is not None:
            clean[k] = v
    return clean


def build_ha_config_data (config):
    payload_sensor = {
        "availability_topic": pub_topic_namespace,
        "device":
            {
            "connections": None,
            "identifiers": ["{}-{}:{}".format(config['mqtt']['client_id'], config['otgw']['host'], config['otgw']['port'])],
            "manufacturer": "Schelte Bron",
            "model": "otgw-nodo",
            "name": "OpenTherm Gateway ({})".format(config['mqtt']['client_id']),
            "sw_version": None,
            "via_device": None
            },
        "device_class": None,
        "expire_after": None,
        "force_update": 'True',
        "icon": None,
        "json_attributes_template": None,
        "json_attributes_topic": None,
        "name": None,
        "payload_available": None,
        "payload_not_available": None,
        "qos": None,
        "state_topic": None,
        "unique_id": None,
        "unit_of_measurement": None,
        "value_template": None,
    }
    # deepcopy
    payload_climate = copy.deepcopy(payload_sensor)
    payload_climate = {**payload_climate, 
        **{
        # "action_template": None,
        # "action_topic": None,
        # "aux_command_topic": None,
        # "aux_state_template": None,
        # "aux_state_topic": None,
        # "away_mode_command_topic": None,
        # "away_mode_state_template": None,
        # "away_mode_state_topic": None,
        "current_temperature_topic": pub_topic_namespace+'/room_temperature/temperature',
        "current_temperature_template": None,
        "initial": '18',
        "max_temp": '24',
        "min_temp": '16',
        # "mode_command_topic": None,
        "mode_state_template": "{% if value == '1' %}heat{% else %}off{% endif %}",
        "mode_state_topic": pub_topic_namespace+'/ch_enabled/state',
        "modes": ['off', 'heat'],
        "precision": 0.1,
        "retain": None,
        "send_if_off": None,
        # using temporary allows local thermostat override. use /constant to block
        # room thermostat input
        "temperature_command_topic": sub_topic_namespace+'/room_setpoint/temporary',
        "temperature_state_template": None,
        "temperature_state_topic": pub_topic_namespace+'/room_setpoint/setpoint',
        "temperature_unit": "C",
        "temp_step": "0.5", 
        "availability":
            {
            "payload_available": None,
            "payload_not_available": None,
            "topic": None,
            },
        "payload_off": 0,
        "payload_on": 1,
        }
    }
    del payload_climate["expire_after"]
    del payload_climate["force_update"]
    del payload_climate["icon"]
    del payload_climate["state_topic"]
    del payload_climate["unit_of_measurement"]

    # deepcopy
    payload_binary_sensor = copy.deepcopy(payload_sensor)
    payload_binary_sensor = {**payload_binary_sensor, 
        **{
        "availability":
            {
            "payload_available": None,
            "payload_not_available": None,
            "topic": None,
            },
        "off_delay": None,
        "payload_off": 0,
        "payload_on": 1,
        }
    }
    del payload_binary_sensor["unit_of_measurement"]
    payload_binary_sensor['device_class'] = 'heat'

    # deepcopy
    payload_sensor_temperature = copy.deepcopy(payload_sensor)
    payload_sensor_temperature['device_class'] = 'temperature'
    payload_sensor_temperature['unit_of_measurement'] = 'C'

    payload_sensor_hours = copy.deepcopy(payload_sensor)
    payload_sensor_hours['device_class'] = None
    payload_sensor_hours['icon'] = 'mdi:clock'
    payload_sensor_hours['unit_of_measurement'] = 'Hours'

    payload_sensor_pressure = copy.deepcopy(payload_sensor)
    payload_sensor_pressure['device_class'] = 'pressure'
    payload_sensor_pressure['unit_of_measurement'] = 'Bar'


    payload_sensor_count = copy.deepcopy(payload_sensor)
    payload_sensor_count['device_class'] = None
    payload_sensor_count['icon'] = 'mdi:counter'
    payload_sensor_count['unit_of_measurement'] = 'x'

    payload_sensor_level = copy.deepcopy(payload_sensor)
    payload_sensor_level['device_class'] = None
    payload_sensor_level['icon'] = 'mdi:percent'
    payload_sensor_level['unit_of_measurement'] = '%'

    payload_mapping = {
        "setpoint": {'ha_type': 'sensor', 'payload': payload_sensor_temperature},
        "temperature": {'ha_type': 'sensor', 'payload': payload_sensor_temperature},
        "hours": {'ha_type': 'sensor', 'payload': payload_sensor_hours},
        "count": {'ha_type': 'sensor', 'payload': payload_sensor_count},
        "level": {'ha_type': 'sensor', 'payload': payload_sensor_level},
        "state": {'ha_type': 'binary_sensor', 'payload': payload_binary_sensor},
        "pressure": {'ha_type': 'sensor', 'payload': payload_sensor_pressure},
    }


    #########
    #########
    #########

    data = []

    # data.append( {'topic': "{}/climate/thermostat/config".format(ha_publish_namespace), 'payload': ''})
    # add thermostat entity
    payload_climate['name'] = "{}_Thermostat".format(config['mqtt']['client_id'])
    payload_climate['unique_id'] = "{}_thermostat".format(config['mqtt']['client_id'])
    data.append( {'topic': "{}/climate/{}/thermostat/config".format(ha_publish_namespace, payload_climate['unique_id'] ), 'payload': json.dumps(cleanNullTerms(payload_climate)) })

    # ID's below are fictive and only used for easy iteration below

    # build list of all entities, use their names
    entity_list = []
    entity_list += [opentherm_ids[x][0] for x in opentherm_ids]                         # opentherm_ids full names
    entity_list += [master_slave_status_bits[x] for x in master_slave_status_bits]      # master_slave_status_bits full names

    # todo: setpoint entities for the water temp setpoints

    for full_name in entity_list:
        
        # dont include id 0: master_slave_status
        if "/" not in full_name:
            continue

        # otgw_type = full_name.split("/")[-1]
        # name = full_name.split("/")[0]
        name, otgw_type = full_name.split("/")
        
        ha_type = payload_mapping[otgw_type]['ha_type']
        payload = copy.deepcopy(payload_mapping[otgw_type]['payload'])

        payload['name'] = "{}_{}".format(config['mqtt']['client_id'],name)
        # need to add the mqtt client name here to make it truely unique
        payload['unique_id'] = "{}_{}".format(config['mqtt']['client_id'],name)
        payload['state_topic'] = "{}/{}".format(pub_topic_namespace, full_name)
        publish_topic = "{}/{}/{}/{}/config".format(ha_publish_namespace, ha_type, payload['unique_id'], name)
        payload = cleanNullTerms(payload)
        payload = json.dumps(payload)
        data.append( {'topic': publish_topic, 'payload': payload, 'retain':'True'})

    return data


class OTGWClient(object):
    r"""
    An abstract OTGW client.

    This class can be used to create implementations of OTGW clients for
    different types of communication protocols and technologies. To create a
    full implementation, only four methods need to be implemented.
    """
    def __init__(self, listener, **kwargs):
        self._worker_running = False
        self._listener = listener
        self._worker_thread = None
        self._send_buffer = collections.deque()

    def open(self):
        r"""
        Open the connection to the OTGW

        Must be overridden in implementing classes. Called before reading of
        the data starts. Should not return until the connection is opened, so
        an immediately following call to `read` does not fail.
        """
        raise NotImplementedError("Abstract method")

    def close(self):
        r"""
        Close the connection to the OTGW

        Must be overridden in implementing classes. Called after reading of
        the data is finished. Should not return until the connection is closed.
        """
        raise NotImplementedError("Abstract method")

    def write(self, data):
        r"""
        Write data to the OTGW

        Must be overridden in implementing classes. Called when a command is
        received that should be sent to the OTGW. Should pass on the data
        as-is, not appending line feeds, carriage returns or anything.
        """
        raise NotImplementedError("Abstract method")

    def read(self, timeout):
        r"""
        Read data from the OTGW

        Must be overridden in implementing classes. Called in a loop while the
        client is running. May return any block of data read from the
        connection, be it line by line or any other block size. Must return a
        string. Line feeds and carriage returns should be passed on unchanged.
        Should adhere to the timeout passed. If only part of a data block is
        read before the timeout passes, return only the part that was read
        successfully, even if it is an empty string.
        """
        raise NotImplementedError("Abstract method")

    def join(self):
        r"""
        Block until the worker thread finishes or exit signal received
        """
        try:
            while self._worker_thread.isAlive():
                self._worker_thread.join(1)
        except SignalExit:
            self.stop()
        except SignalAlarm:
            self.reconnect()

    def start(self):
        r"""
        Connect to the OTGW and start reading data
        """
        if self._worker_thread:
            raise RuntimeError("Already running")
        self._worker_thread = Thread(target=self._worker)
        self._worker_thread.start()
        log.info("Started worker thread #%s", self._worker_thread.ident)

    def stop(self):
        r"""
        Stop reading data and disconnect from the OTGW
        """
        if not self._worker_thread:
            raise RuntimeError("Not running")
        log.info("Stopping worker thread #%s", self._worker_thread.ident)
        self._worker_running = False
        self._worker_thread.join()

    def reconnect(self, reconnect_pause=10):
        r"""
        Attempt to reconnect when the connection is lost
        """
        try:
            self.close()
        except Exception:
            pass

        while self._worker_running:
            try:
                self.open()
                self._listener((pub_topic_namespace, 'online'))
                break
            except Exception:
                self._listener((pub_topic_namespace, 'offline'))
                log.warning("Waiting %d seconds before retrying", reconnect_pause)
                sleep(reconnect_pause)

    def send(self, data):
        self._send_buffer.append(data)

    def _worker(self):
        # _worker_running should be True while the worker is running
        self._worker_running = True

        try:
          # Open the connection to the OTGW
           self.open()
        except ConnectionException:
           log.warning("Retrying immediately")
           self.reconnect()

        # Compile a regex that will only match the first part of a string, up
        # to and including the first time a line break and/or carriage return
        # occurs. Match any number of line breaks and/or carriage returns that
        # immediately follow as well (effectively discarding empty lines)
        line_splitter = re.compile(r'^.*[\r\n]+')

        # Create a buffer for read data
        data = ""

        while self._worker_running:
            log.debug("Worker run with initial data: '%s'", data)
            try:
                # Send MQTT messages to TCP serial
                while self._send_buffer:
                    self.write(self._send_buffer[0])
                    self._send_buffer.popleft()
                # Receive TCP serial data for MQTT
                read = self.read(timeout=0.5)
                if read:
                    data += read
            except ConnectionException:
                self.reconnect()
            # Find all the lines in the read data

            log.debug("Retrieved data: '%s'", data)
            while True:
                m = line_splitter.match(data)
                if not m:
                    log.debug("Unable to extract line from data '%s'", data)
                    # There are no full lines yet, so we have to read some more
                    break
                log.debug("Extracted line: '%s'", m.group())

                raw_message = m.group().rstrip('\r\n')
                # Get all the messages for the line that has been read,
                # most lines will yield no messages or just one, but
                # flags-based lines may return more than one.
                log.debug("Raw message: %s", raw_message)
                for msg in get_messages(raw_message):
                    try:
                        # Pass each message on to the listener
                        log.debug("Execute message: '%s'", msg)
                        self._listener(msg)
                    except Exception as e:
                        # Log a warning when an exception occurs in the
                        # listener
                        log.exception("Error in listener handling for message '%s', jump to close and reconnect: %s", raw_message, str(e))

                # Strip the consumed line from the buffer
                data = data[m.end():]
                log.debug("Left data: '%s'", data)

        # After the read loop, close the connection and clean up
        self.close()
        self._worker_thread = None

class ConnectionException(Exception):
    pass

class SignalExit(Exception):
    """
    Custom exception which is used to trigger the clean exit
    of all running threads and the main program.
    """
    pass

class SignalAlarm(Exception):
    """
    Custom exception upon trigger of SIGALRM signal
    """
    pass
