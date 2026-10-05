#!/usr/bin/env bashio
set -eu

OPTIONS="/data/options.json"
MPP="/opt/inverter-multi-protocol/bin/mpp-solar"

get_option() {
    python3 -c "import json; print(json.load(open('$OPTIONS')).get('$1', '$2'))"
}

INVERTER_NAME="$(get_option inverter_name INVERTER_1)"
PROTOCOL="$(get_option protocol PI30)"
PORT="$(get_option port /dev/ttyUSB0)"
POLL_INTERVAL="$(get_option poll_interval 5)"

echo "--------------------------------------------------"
echo " Inverter Multi-Protocol 0.1.1"
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
echo "Starting read-only monitoring..."
echo

while true
do
    "$MPP" \
        -n "${INVERTER_NAME}" \
        -p "${PORT}" \
        -P "${PROTOCOL}" \
        -b 2400 \
        -c QPIGS \
        -o hass_mqtt \
        -q "${MQTT_HOST}" \
        --mqttport "${MQTT_PORT}" \
        --mqttuser "${MQTT_USERNAME:-}" \
        --mqttpass "${MQTT_PASSWORD:-}" \
        || echo "WARNING: inverter polling failed"

    sleep "${POLL_INTERVAL}"
done
