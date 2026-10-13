#!/usr/bin/env python3
"""plex.py — what the "Plex" voice device does: wake, sleep or report cinema.

    plex.py wake     broadcast a Wake-on-LAN packet to cinema (bastion is on the physical LAN,
                     and this container shares its network namespace, so the broadcast works)
    plex.py sleep    ssh robby@cinema with the dedicated alexa key; cinema's authorized_keys
                     pins that key to its forced command, so the only thing this can ever do
                     there is `suspend`
    plex.py status   exit 0 if cinema answers on port 22 (awake), 1 otherwise -- fauxmo's
                     state command, so "Alexa, is Plex on?" answers truthfully

Called by fauxmo (bastion/alexa/config.json) when an Echo says "turn Plex on/off". Logs one
line per action to /log/alexa.log, which is ~/logs/alexa.log on bastion.
"""
import os
import socket
import subprocess
import sys
import time

CINEMA_IP = "192.168.0.13"
CINEMA_MAC = "d0:17:c2:dd:c1:c2"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
       "-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=/state/known_hosts",
       "-i", "/keys/alexa_ed25519", f"robby@{CINEMA_IP}"]
LOG = "/log/alexa.log"


def log(msg):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} [alexa-plex] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def online():
    try:
        with socket.create_connection((CINEMA_IP, 22), timeout=1.5):
            return True
    except OSError:
        return False


def wake():
    packet = b"\xff" * 6 + bytes.fromhex(CINEMA_MAC.replace(":", "")) * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for _ in range(3):
            s.sendto(packet, ("255.255.255.255", 9))
    log(f"wake: magic packet sent to {CINEMA_MAC}" + (" (already online)" if online() else ""))


def sleep():
    if not online():
        log("sleep: cinema already offline")
        return 0
    r = subprocess.run(SSH + ["suspend"], capture_output=True, text=True, timeout=30)
    log(f"sleep: ssh exit {r.returncode} {r.stderr.strip()[:120]}")
    return r.returncode


if __name__ == "__main__":
    verb = sys.argv[1] if len(sys.argv) == 2 else ""
    if verb == "wake":
        wake()
    elif verb == "sleep":
        sys.exit(sleep())
    elif verb == "status":
        sys.exit(0 if online() else 1)
    else:
        print(__doc__)
        sys.exit(2)
