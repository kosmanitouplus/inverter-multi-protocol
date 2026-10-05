#!/command/with-contenv bashio
set -eu

OPTIONS="/data/options.json"
MPP="/opt/inverter-multi-protocol/bin/mpp-solar"
CONFIG="/tmp/mpp-solar.conf"
PIDFILE="/tmp/mpp-solar.pid"

get_option() {
    python3 -c "import json; print(json.load(open('$OPTIONS')).get('$1', '$2'))"
}

INVERTER_NAME="$(get_option inverter_name INVERTER_1)"
PROTOCOL="$(get_option protocol PI30)"
PORT="$(get_option port /dev/ttyUSB0)"
POLL_INTERVAL="$(get_option poll_interval 5)"

echo "--------------------------------------------------"
echo " Inverter Multi-Protocol 0.1.4"
echo "--------------------------------------------------"
echo "Name       : ${INVERTER_NAME}"
echo "Protocol   : ${PROTOCOL}"
echo "Serial port: ${PORT}"
echo "Interval   : ${POLL_INTERVAL}s"
echo

if [ ! -e "${PORT}" ]; then
    echo "ERROR: Serial port not found:"
    echo "${PORT}"
    exit 1
fi

MQTT_HOST="$(bashio::services mqtt "host")"
MQTT_PORT="$(bashio::services mqtt "port")"
MQTT_USERNAME="$(bashio::services mqtt "username")"
MQTT_PASSWORD="$(bashio::services mqtt "password")"

if [ -z "${MQTT_HOST}" ]; then
    echo "ERROR: MQTT service information is unavailable."
    exit 1
fi

echo "MQTT       : ${MQTT_HOST}:${MQTT_PORT}"
echo "Mode       : persistent daemon"
echo "Access     : read-only"
echo

python3 - "${CONFIG}" \
    "${POLL_INTERVAL}" \
    "${MQTT_HOST}" \
    "${MQTT_PORT}" \
    "${MQTT_USERNAME}" \
    "${MQTT_PASSWORD}" \
    "${INVERTER_NAME}" \
    "${PORT}" \
    "${PROTOCOL}" <<'PY'
import configparser
import sys

(
    config_path,
    pause,
    mqtt_host,
    mqtt_port,
    mqtt_user,
    mqtt_pass,
    inverter_name,
    port,
    protocol,
) = sys.argv[1:]

config = configparser.ConfigParser()

config["SETUP"] = {
    "pause": pause,
    "mqtt_broker": mqtt_host,
    "mqtt_port": mqtt_port,
    "mqtt_user": mqtt_user,
    "mqtt_pass": mqtt_pass,
}

config[inverter_name] = {
    "port": port,
    "protocol": protocol,
    "baud": "2400",
    "command": "QPIGS",
    "tag": inverter_name,
    "outputs": "hass_mqtt",
}

with open(config_path, "w") as f:
    config.write(f)
PY

chmod 600 "${CONFIG}"

echo "Configuration generated."
echo "Starting persistent mpp-solar process..."
echo

exec "${MPP}" \
    -C "${CONFIG}" \
    --daemon \
    --pidfile "${PIDFILE}" \
    -I
