#!/command/with-contenv bashio
set -eu
# Keep explicit credentials as a fallback when Supervisor service info is absent.
if bashio::services.available mqtt; then
    export MQTT_HOST="$(bashio::services mqtt host)"
    export MQTT_PORT="$(bashio::services mqtt port)"
    export MQTT_USER="$(bashio::services mqtt username)"
    export MQTT_PASSWORD="$(bashio::services mqtt password)"
fi
cd /app
exec /opt/inverter-multi-protocol/bin/python3 -m inverter_runtime
