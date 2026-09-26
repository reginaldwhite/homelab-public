#!/bin/bash
# sleep-vault.sh — arm Wake-on-LAN, verify it took, then shut vault down cleanly.
#
# Arms immediately before powering off rather than trusting boot-time state. The WoL
# bit only has to be correct at the moment of shutdown, and vault's boot-time arming
# races with Unraid bringing the bond up — observed winning on one boot and losing on
# the next. Arming here takes that race out of the path that actually matters.
#
# **It refuses to power vault down if it cannot verify the arming.** A vault that is
# off and unwakeable needs someone to walk over and press a button. A vault that is
# still running costs a few watts and can be dealt with whenever. Those are not
# symmetrical, so this fails towards the recoverable one.
#
# `powerdown` rather than `shutdown`: it stops the array first, so the next boot does
# not begin a parity check.
#
#   sleep-vault.sh            arm, verify, power down
#   sleep-vault.sh --dry-run  arm and verify only — leaves vault running

DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/vault-common.sh"

LOG="/home/robby/logs/power.log"

mkdir -p "$(dirname "$LOG")"
log() { echo "$(date -Iseconds) [sleep-vault] $*" >> "$LOG"; }

if ! vault_pings; then
    log "vault already down, nothing to do"
    exit 0
fi

# wol-arm.sh lives on vault's flash and handles all three requirements: the NIC, its
# PCIe root port, and the kernel's per-device flag. It is idempotent.
if ! vault_ssh "bash /boot/config/wol-arm.sh" 2>/dev/null; then
    log "ERROR: could not run /boot/config/wol-arm.sh on vault — refusing to power it down"
    exit 1
fi

# Verify rather than trust. Both the card and the root port have to be right; either
# one alone produces a machine that cannot be woken.
state=$(vault_ssh '
    for i in eth0 eth1; do ethtool $i 2>/dev/null | grep -q "Wake-on: g" && echo "nic:$i"; done
    grep -qE "^P0P6[[:space:]].*enabled" /proc/acpi/wakeup && echo "port:P0P6"
    grep -qE "^P0P9[[:space:]].*enabled" /proc/acpi/wakeup && echo "port:P0P9"
' 2>/dev/null)

if ! grep -q "^nic:" <<<"$state" || ! grep -q "^port:" <<<"$state"; then
    log "ERROR: vault is not armed for Wake-on-LAN (got: $(tr '\n' ' ' <<<"$state")) — refusing to power it down, because nothing could wake it again"
    exit 1
fi
log "armed for WoL ($(tr '\n' ' ' <<<"$state"))"

if [[ $DRY -eq 1 ]]; then
    echo "armed and verified: $(tr '\n' ' ' <<<"$state")"
    echo "--dry-run: leaving vault running"
    log "dry run, not powering down"
    exit 0
fi

log "sending powerdown to vault..."
vault_ssh "nohup /usr/local/sbin/powerdown >/tmp/powerdown.log 2>&1 &" 2>/dev/null

SECONDS=0
while (( SECONDS < 300 )); do
    vault_pings || { log "vault is down (${SECONDS}s)"; exit 0; }
    sleep 5
done
log "ERROR: vault still answering ping 300s after powerdown"
exit 1
