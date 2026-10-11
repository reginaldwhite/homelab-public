#!/bin/bash
# wake-forge.sh — Send Wake-on-LAN magic packet to forge (7700k), then confirm it boots
# Runs natively on bastion (file) so can broadcast directly on the LAN.
# Forge's NIC must be armed for WoL (see README): a packet sent to a machine that
# isn't listening is silently dropped, so success is judged by forge answering ping.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/textfile-metrics.sh"

FORGE_IP="192.168.0.12"
FORGE_MAC="88:d7:f6:13:14:3a"
BOOT_TIMEOUT=300   # seconds; a normal boot answers ping in well under a minute
LOG="/home/robby/logs/power.log"

mkdir -p "$(dirname "$LOG")"

forge_up() { ping -c1 -W2 "$FORGE_IP" &>/dev/null; }

if forge_up; then
    echo "$(date -Iseconds) [wake-forge] Forge already up, nothing to do" >> "$LOG"
    set_expected_up forge 1
    exit 0
fi

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

if ! send_wol >/dev/null; then
    echo "$(date -Iseconds) [wake-forge] ERROR: could not send WoL packet" >> "$LOG"
    exit 1
fi
echo "$(date -Iseconds) [wake-forge] WoL sent to forge ($FORGE_MAC), waiting for it to boot..." >> "$LOG"

# A broadcast can be lost, and the NIC in S5 occasionally needs a second one (the 05:00 wake
# failed on 2026-10-10 after two clean mornings, with nothing wrong on the forge side), so
# the packet is repeated every minute while waiting rather than sent once.
SECONDS=0
next_send=60
while (( SECONDS < BOOT_TIMEOUT )); do
    if forge_up; then
        echo "$(date -Iseconds) [wake-forge] Forge is up (${SECONDS}s)" >> "$LOG"
        set_expected_up forge 1
        "$HERE/event.sh" power wake-forge ok --detail "{\"seconds\": $SECONDS}"
        exit 0
    fi
    if (( SECONDS >= next_send )); then
        send_wol >/dev/null && echo "$(date -Iseconds) [wake-forge] WoL re-sent (${SECONDS}s, no ping yet)" >> "$LOG"
        next_send=$(( next_send + 60 ))
    fi
    sleep 5
done
echo "$(date -Iseconds) [wake-forge] ERROR: forge did not answer ping within ${BOOT_TIMEOUT}s of WoL" >> "$LOG"
"$HERE/event.sh" power wake-forge error --detail "{\"reason\": \"no ping within ${BOOT_TIMEOUT}s\"}"
exit 1
