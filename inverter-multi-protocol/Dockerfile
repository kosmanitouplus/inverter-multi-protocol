ARG BUILD_FROM=ghcr.io/home-assistant/base:3.22
FROM ${BUILD_FROM}
ARG BUILD_VERSION=0.3.0-rc1
ARG BUILD_ARCH
LABEL io.hass.version="${BUILD_VERSION}" io.hass.type="addon" io.hass.arch="${BUILD_ARCH}"
RUN apk add --no-cache python3~3.12 py3-pip
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python3 -m venv /opt/inverter-multi-protocol \
    && /opt/inverter-multi-protocol/bin/pip install --no-cache-dir -r /app/requirements.txt
COPY inverter_runtime /app/inverter_runtime
COPY run.sh /run.sh
RUN chmod a+x /run.sh
CMD ["/run.sh"]
