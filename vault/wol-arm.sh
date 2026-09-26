#!/bin/bash
# wol-arm.sh — arm Wake-on-LAN on vault. Idempotent; safe to run repeatedly.
#
# NOT DEPLOYED. vault is not a deploy.sh target (its root filesystem is RAM and its
# flash cannot carry an executable bit), so this lives at /boot/config/wol-arm.sh on
# vault and is installed by hand. This copy exists because that flash is a USB stick
# with no other backup, and because a hand-installed file with no version history is
# how configuration quietly drifts.
#
# To (re)install:
#   scp vault/wol-arm.sh root@192.168.0.212:/boot/config/wol-arm.sh
# and make sure /boot/config/go calls it — see docs/vault-power.md.
#
# THREE things must be true, and only the first is the one anyone mentions:
#   1. the NIC is armed for magic packets           -> ethtool
#   2. the NIC's PCIe ROOT PORT may raise a wake    -> /proc/acpi/wakeup
#   3. the kernel per-device wake flag is set       -> sysfs (ethtool sets this)
# Missing (2) looks exactly like a correct configuration that never wakes: the card
# reports Wake-on: g, the BIOS option is enabled, the packet arrives, and the wake
# event dies one hop up at the bridge. That cost four rounds of testing to find.

for i in eth0 eth1; do
    /usr/sbin/ethtool -s "$i" wol g 2>/dev/null
done

# P0P6 = 00:1c.2 (eth0), P0P9 = 00:1c.5 (eth1). Writing the name TOGGLES the entry,
# so only write when it is currently disabled — otherwise this turns wake back off.
for d in P0P6 P0P9; do
    if grep -qE "^$d[[:space:]].*disabled" /proc/acpi/wakeup; then
        echo "$d" > /proc/acpi/wakeup
    fi
done

# Exit 0 explicitly. Without this the script returns the status of the last grep,
# which FAILS when everything is already armed -- the normal case. sleep-vault.sh
# treats a non-zero exit as "could not arm" and refuses to power vault down, so that
# one missing line meant vault would never have shut down again.
exit 0
