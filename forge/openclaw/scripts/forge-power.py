#!/usr/bin/env python3
"""
forge-power.py — Wake or sleep the forge server (7700k).

Usage:
  python3 forge-power.py wake           # Send WoL magic packet
  python3 forge-power.py sleep          # Shut down via SSH
  python3 forge-power.py status         # Ping check (online/offline)

Note: forge runs on a scheduled 5am–11pm window managed by bastion cron.
Use this script only for on-demand control outside normal hours.
"""

import socket
import subprocess
import sys
import time

FORGE_IP   = "192.168.0.12"
FORGE_MAC  = "88:d7:f6:13:14:3a"
FORGE_USER = "robby"
# bastion (file) relays the WoL broadcast since we're in a Docker bridge network
BASTION_IP = "192.168.0.10"

# known_hosts lives under the agent's state dir because that is the only writable path
# that survives the container being recreated; /home/node/.ssh holds nothing but the
# read-only key mount. accept-new pins each host on first contact and refuses a changed
# key afterwards — the previous StrictHostKeyChecking=no with UserKnownHostsFile=/dev/null
# accepted whatever answered on the address, on every single call.
SSH_OPTS = [
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "UserKnownHostsFile=/home/node/.openclaw/known_hosts",
    "-o", "ConnectTimeout=10",
    "-i", "/home/node/.ssh/openclaw_ed25519",
]


def send_wol(mac: str) -> None:
    """Send WoL magic packet via bastion (which is on the physical LAN)."""
    result = subprocess.run(
        ["ssh", *SSH_OPTS, f"robby@{BASTION_IP}", "wake-forge"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"SSH to bastion failed: {result.stderr.strip()}")
    print(f"Wake-on-LAN magic packet sent to {mac}")


def is_online(ip: str, timeout: float = 1.0) -> bool:
    """Return True if the host responds to a TCP connection on port 22."""
    try:
        with socket.create_connection((ip, 22), timeout=timeout):
            return True
    except OSError:
        return False


def cmd_wake() -> None:
    send_wol(FORGE_MAC)
    print("Magic packet sent — forge is booting. Use 'status' in ~60s to confirm it's online.")


def cmd_sleep() -> None:
    print(f"Sending shutdown to forge ({FORGE_IP})...")
    result = subprocess.run(
        ["ssh", *SSH_OPTS, f"{FORGE_USER}@{FORGE_IP}", "shutdown"],
        capture_output=True,
        text=True,
    )
    if result.returncode in (0, 255):
        print("Shutdown command accepted. Forge is powering off.")
    else:
        print(f"SSH error (exit {result.returncode}): {result.stderr.strip()}")
        sys.exit(result.returncode)


def cmd_status() -> None:
    if is_online(FORGE_IP):
        print(f"Forge ({FORGE_IP}) is ONLINE")
    else:
        print(f"Forge ({FORGE_IP}) is OFFLINE")


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
