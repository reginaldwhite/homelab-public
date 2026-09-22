#!/bin/bash
# wake-forge.sh — Send Wake-on-LAN magic packet to forge (7700k)
# Runs natively on bastion (file) so can broadcast directly on the LAN.

FORGE_MAC="88:d7:f6:13:14:3a"
LOG="/home/robby/logs/power.log"

mkdir -p "$(dirname "$LOG")"

send_wol() {
    if command -v wakeonlan &>/dev/null; then
        wakeonlan "$FORGE_MAC"
    elif command -v etherwake &>/dev/null; then
        etherwake "$FORGE_MAC"
    else
        python3 - <<EOF
import socket
mac = "$FORGE_MAC".replace(":", "")
payload = bytes.fromhex("ff" * 6 + mac * 16)
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
s.sendto(payload, ("192.168.0.255", 9))
s.sendto(payload, ("255.255.255.255", 9))
print("WoL magic packet sent")
EOF
    fi
}

send_wol && echo "$(date -Iseconds) [wake-forge] WoL sent to forge ($FORGE_MAC)" >> "$LOG"
