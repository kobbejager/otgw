import argparse
import opentherm
from opentherm import SignalExit, SignalAlarm
import datetime
import logging
import signal
import json
import paho.mqtt.client as mqtt
import paho.mqtt.publish as publish
import sys

# Values used to parse boolean values of incoming messages
true_values=('True', 'true', '1', 'y', 'yes')
false_values=('False', 'false', '0', 'n', 'no')

# Default settings
settings = {
    "otgw" : {
        "type": "serial",
        "device": "/dev/ttyUSB0",
        "baudrate": 9600,
        "data_timeout": 20
    },
    "mqtt" : {
        "client_id": "otgw",
        "host": "127.0.0.1",
        "port": 1883,
        "keepalive": 60,
        "bind_address": "",
        "username": None,
        "password": None,
        "qos": 0,
        "pub_topic_namespace": "value/otgw",
        "sub_topic_namespace": "set/otgw",
        "retain": False,
        "changed_messages_only": False,
        "homeassistant": None
    }
}

# Parse arguments
parser = argparse.ArgumentParser(description="Python OTGW MQTT bridge")
parser.add_argument("-c", "--config", default="config.json", help="Configuration file (default: %(default)s)")
parser.add_argument("-l", "--loglevel", default="INFO", help="Event level to log (default: %(default)s)")
parser.add_argument("-v", "--verbose", action='store_true', help="Enable MQTT logger")
parser.add_argument("-ha", "--publish-ha-config-only", action='store_true', help="Send config to HA via MQTT w/o connecting to the OTGW")
args = parser.parse_args()
# print(args)

# Parse log level
num_level = getattr(logging, args.loglevel.upper(), None)
if not isinstance(num_level, int):
    raise ValueError('Invalid log level: %s' % args.loglevel)

# Setup signal handlers
def sig_exit_handler(signal, frame):
    logging.warning("Exiting on signal %r", signal)
    raise SignalExit

signal.signal(signal.SIGINT, sig_exit_handler)
signal.signal(signal.SIGTERM, sig_exit_handler)

def sig_alarm_handler(signal, frame):
    logging.warning("No data received after %d seconds.", settings['otgw']['data_timeout'])
    raise SignalAlarm

signal.signal(signal.SIGALRM, sig_alarm_handler)

# Update default settings from the settings file
with open(args.config) as f:
    overrides = json.load(f)
    if 'otgw' in overrides and isinstance(overrides['otgw'], dict):
        settings['otgw'].update(overrides['otgw'])
    if 'mqtt' in overrides and isinstance(overrides['mqtt'], dict):
        settings['mqtt'].update(overrides['mqtt'])

# Set the namespace of the mqtt messages from the settings
opentherm.pub_topic_namespace=settings['mqtt']['pub_topic_namespace']
opentherm.sub_topic_namespace=settings['mqtt']['sub_topic_namespace']

# Set up logging
log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
logging.basicConfig(level=num_level, format=log_format)
log = logging.getLogger(__name__)
log.info('Loglevel is %s', logging.getLevelName(log.getEffectiveLevel()))

# Store messages (and publish only changed values on mqtt)
if settings['mqtt']['changed_messages_only']:
    stored_messages = {}

def on_mqtt_connect(client, userdata, flags, rc):
    # Subscribe to all topics in our namespace when we're connected. Send out
    # a message telling we're online
    log.info("MQTT:Connected with result code %s", rc)
    mqtt_client.subscribe('{}/#'.format(settings['mqtt']['sub_topic_namespace']))
    mqtt_client.subscribe('{}'.format(settings['mqtt']['sub_topic_namespace']))
    mqtt_client.publish(
        topic=opentherm.pub_topic_namespace,
        payload="online",
        qos=settings['mqtt']['qos'],
        retain=True)

    # if we're using HA, send the HA discovery messages
    if settings['mqtt']['homeassistant']:
        send_HA_discovery()

def on_mqtt_message(client, userdata, msg):
    # Handle incoming messages
    log.info("Received message on topic {} with payload {}".format(
        msg.topic, 
        str(msg.payload.decode('ascii', 'ignore'))))
    namespace = settings['mqtt']['sub_topic_namespace']
    command_generators={
        "{}/room_setpoint/temporary".format(namespace): \
            lambda _ :"TT={:.2f}".format(float(_) if is_float(_) else 0),
        "{}/room_setpoint/constant".format(namespace):  \
            lambda _ :"TC={:.2f}".format(float(_) if is_float(_) else 0),
        "{}/outside_temperature".format(namespace):     \
            lambda _ :"OT={:.2f}".format(float(_) if is_float(_) else 99),
        "{}/hot_water/enable".format(namespace):        \
            lambda _ :"HW={}".format('1' if _ in true_values else '0' if _ in false_values else 'T'),
        "{}/hot_water/temperature".format(namespace):   \
            lambda _ :"SW={:.2f}".format(float(_) if is_float(_) else 60),
        "{}/central_heating/enable".format(namespace):  \
            lambda _ :"CH={}".format('0' if _ in false_values else '1'),
        "{}/central_heating/temperature".format(namespace):   \
            lambda _ :"SH={:.2f}".format(float(_) if is_float(_) else 60),
        "{}/control_setpoint".format(namespace):   \
            lambda _ :"CS={:.2f}".format(float(_) if is_float(_) else 60),
        "{}/max_modulation".format(namespace):  \
            lambda _ :"MM={:d}".format(int(_) if is_int(_) else 100),
        "{}/cmd".format(namespace):  \
            lambda _ :_.strip(),
        # TODO: "set/otgw/raw/+": lambda _ :publish_to_otgw("PS", _)
    }
    # Find the correct command generator from the dict above
    command_generator = command_generators.get(msg.topic)
    if command_generator:
        # Get the command and send it to the OTGW
        command = command_generator(msg.payload.decode('ascii', 'ignore'))
        log.info("Sending command: '{}'".format(command))
        otgw_client.send("{}\r".format(command))

def on_otgw_message(message):
    if args.verbose:
        log.debug("message: [type: %s] %s", type(message), message)
    if type(message) is not tuple:
        log.error("interal malformed message received - message was probably incorrectly parsed")
        return
    # Force retain for device state
    if message[0] == opentherm.pub_topic_namespace and (message[1] == 'online' or message[1] == 'offline'):
        retain=True
    else:
        retain=settings['mqtt']['retain']
        # Reset alarm when OTGW data is received
        signal.alarm(settings['otgw']['data_timeout'])
    
    # In case the option changed_messages_only is enabled: only those that have changed
    if settings['mqtt']['changed_messages_only']:
        # If the topic exists in the stored messages dict, and is unchanged, don't send out message
        if message[0] in stored_messages:
            if stored_messages[message[0]] == message[1]:
                return
        # Update stored messages dict
        stored_messages[message[0]] = message[1]
    # Send out messages to the MQTT broker
    mqtt_client.publish(
        topic=message[0],
        payload=message[1],
        qos=settings['mqtt']['qos'],
        retain=retain)

def send_HA_discovery(publish_ha_config_only: bool = False):
    log.error("sub in mod 2: {}".format(opentherm.sub_topic_namespace))

    messages = opentherm.build_ha_config_data(settings)

    if publish_ha_config_only:
        log.info("send HA discovery via publish.multiple")
        connection_settings= dict(
            # qos=settings['mqtt']['qos'],
            client_id=settings['mqtt']['client_id'],
            # retain=False,
            hostname=settings['mqtt']['host'],
            port=settings['mqtt']['port'],
            keepalive=settings['mqtt']['keepalive'],
            # bind_address=settings['mqtt']['bind_address']
            )

        if settings['mqtt']['username']:
            connection_settings.update(
                dict(
                    username=settings['mqtt']['username'], 
                    password=settings['mqtt']['password']
                    )
                )

        publish.multiple(
            msgs=messages,
            **connection_settings
        )
    else:
        log.info("send HA discovery via mqtt_client")

        for entity in messages:
            # Send out messages to the MQTT broker
            if args.verbose:
                log.debug("send HA discovery: {} ".format(entity))
            mqtt_client.publish(
                topic=entity['topic'],
                payload=entity['payload'],
                qos=settings['mqtt']['qos'],
                retain=True)


def is_float(value):
    try:
        float(value)
        return True
    except ValueError:
        return False

def is_int(value):
    try:
        int(value)
        return True
    except ValueError:
        return False

log.info("Initializing MQTT")

# publish config to HA
# if settings['mqtt']['homeassistant']:
    # log.info("publishing HA config")
    # send_HA_discovery()
if args.publish_ha_config_only:
    if not settings['mqtt']['homeassistant']:
        log.error('homeassistant is not enabled in the config!')
    else:
        send_HA_discovery(publish_ha_config_only=True)
        log.error("pushed config -- quit")
    sys.exit(0)

# Set up paho-mqtt
mqtt_client = mqtt.Client(
    client_id=settings['mqtt']['client_id'])
if args.verbose:
    mqtt_client.enable_logger()
mqtt_client.on_connect = on_mqtt_connect
mqtt_client.on_message = on_mqtt_message

if settings['mqtt']['username']:
    mqtt_client.username_pw_set(
        settings['mqtt']['username'],
        settings['mqtt']['password'])

# The will makes sure the device registers as offline when the connection
# is lost
mqtt_client.will_set(
    topic=opentherm.pub_topic_namespace,
    payload="offline",
    qos=settings['mqtt']['qos'],
    retain=True)

# Let's not wait for the connection, as it may not succeed if we're not
# connected to the network or anything. Such is the beauty of MQTT
mqtt_client.connect_async(
    host=settings['mqtt']['host'],
    port=settings['mqtt']['port'],
    keepalive=settings['mqtt']['keepalive'],
    bind_address=settings['mqtt']['bind_address'])
mqtt_client.loop_start()

log.info("Initializing OTGW")

# Import the module for the correct gateway type and return a reference to
# the type itself, so we can instantiate it easily
otgw_type = {
    "serial" : lambda: __import__('opentherm_serial',
                              globals(), locals(), ['OTGWSerialClient'], 0) \
                              .OTGWSerialClient,
    "tcp" :    lambda: __import__('opentherm_tcp',
                              globals(), locals(), ['OTGWTcpClient'], 0) \
                              .OTGWTcpClient,
                                  # This is actually not implemented yet
}[settings['otgw']['type']]()

# Create the actual instance of the client
otgw_client = otgw_type(on_otgw_message, **settings['otgw'])

# Start the gateway client's worker thread
otgw_client.start()
# Block until the gateway client is stopped
otgw_client.join()
