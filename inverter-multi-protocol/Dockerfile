FROM ghcr.io/home-assistant/base:latest

RUN apk add --no-cache \
    python3 \
    py3-pip

RUN python3 -m venv /opt/inverter-multi-protocol \
    && /opt/inverter-multi-protocol/bin/pip install --no-cache-dir \
       "mppsolar==0.16.56"

COPY patches/hass_mqtt.py /opt/inverter-multi-protocol/lib/python3.14/site-packages/mppsolar/outputs/hass_mqtt.py

COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
