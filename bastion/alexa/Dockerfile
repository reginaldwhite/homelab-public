# alexa: ha-bridge, an emulated Philips Hue bridge the Echos discover and control over the LAN
# (README, "Alexa"). The first version of this used fauxmo (a WeMo emulation): current Echo
# firmware discovers a fake WeMo but sends its commands to Belkin's cloud, so nothing ever
# reached us. Echos still talk to Hue bridges locally.
#
# ha-bridge is a Java 11 jar, last released in 2022 and still what people use for this; the
# release is pinned by version and fetched from GitHub at build time (there is no official
# image). python3 + openssh-client are for plex.py, which ha-bridge runs as the device's
# on/off action. Runs as root because the Echo requires the Hue bridge on port 80.
FROM eclipse-temurin:11-jre-alpine
ARG HA_BRIDGE_VERSION=5.4.1
RUN apk add --no-cache python3 openssh-client curl \
 && mkdir -p /ha-bridge/data \
 && curl -fsSL -o /ha-bridge/ha-bridge.jar \
      "https://github.com/bwssytems/ha-bridge/releases/download/v${HA_BRIDGE_VERSION}/ha-bridge-${HA_BRIDGE_VERSION}-java11.jar"
WORKDIR /app
COPY plex.py ./
WORKDIR /ha-bridge
CMD ["java", "-jar", "-Dconfig.file=/ha-bridge/data/habridge.config", "-Dserver.port=80", "/ha-bridge/ha-bridge.jar"]
