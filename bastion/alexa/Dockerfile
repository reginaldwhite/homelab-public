# alexa: fauxmo, an emulated Belkin WeMo socket the Echos discover on the LAN (docs: README,
# "Alexa"). One pinned dependency; the SSH client is for the sleep verb (see plex.py).
# Runs as root inside the container because SSDP discovery needs port 1900, and it shares
# bastion's network namespace for the same reason (broadcast discovery, WoL broadcast).
FROM python:3.12-alpine
RUN apk add --no-cache openssh-client && pip install --no-cache-dir fauxmo==0.8.0
WORKDIR /app
COPY plex.py config.json ./
CMD ["fauxmo", "-c", "/app/config.json", "-vv"]
