#!/usr/bin/env python3
"""
plex-power.py — Wake or sleep the Plex server.

Usage:
  python3 plex-power.py wake           # Send WoL magic packet
  python3 plex-power.py sleep          # Suspend via SSH
  python3 plex-power.py status         # Ping check (online/offline)
"""

import socket
import subprocess
import sys

PLEX_IP  = "192.168.0.13"
PLEX_MAC = "d0:17:c2:dd:c1:c2"
PLEX_USER = "robby"          # SSH username on cinema
BASTION_IP = "192.168.0.10"  # Relay host for WoL broadcast


def send_wol(mac: str) -> None:
    """Send WoL by SSHing to the host machine (on the physical LAN).
    The container runs on a Docker bridge and cannot broadcast to 192.168.0.255
    directly, so we SSH to the host at 192.168.0.12 and run the packet send there.
    """
    result = subprocess.run(
        [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=10",
            "-i", "/home/node/.ssh/openclaw_ed25519",
            f"robby@{BASTION_IP}",
            "wake-cinema",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"SSH to host failed: {result.stderr.strip()}")
    print(f"Wake-on-LAN magic packet sent to {mac}")


def is_online(ip: str, timeout: float = 1.0) -> bool:
    """Return True if the host responds to a TCP connection on port 22."""
    try:
        with socket.create_connection((ip, 22), timeout=timeout):
            return True
    except OSError:
        return False


def cmd_wake() -> None:
    send_wol(PLEX_MAC)
    print("Magic packet sent — Plex server is booting. Use 'status' in ~30s to confirm it's online.")


def cmd_sleep() -> None:
    print(f"Sending suspend command to {PLEX_IP}...")
    result = subprocess.run(
        [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=10",
            "-i", "/home/node/.ssh/openclaw_ed25519",
            f"{PLEX_USER}@{PLEX_IP}",
            "suspend",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print("Suspend command sent. Plex server is going to sleep.")
    else:
        print(f"SSH error (exit {result.returncode}): {result.stderr.strip()}")
        print("Make sure SSH key auth is set up for the Plex server.")
        sys.exit(result.returncode)


def cmd_status() -> None:
    if is_online(PLEX_IP):
        print(f"Plex server ({PLEX_IP}) is ONLINE")
    else:
        print(f"Plex server ({PLEX_IP}) is OFFLINE")


COMMANDS = {
    "wake":   cmd_wake,
    "sleep":  cmd_sleep,
    "status": cmd_status,
}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    COMMANDS[sys.argv[1]]()
