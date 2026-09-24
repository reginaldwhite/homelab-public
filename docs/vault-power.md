# Plan: replace vault's smart plug with Wake-on-LAN

> **Tested 2026-09-24: it does not work today.** The NIC was armed (`Wake-on: g` confirmed on
> both interfaces), the setting was persisted in `/boot/config/go`, vault was shut down cleanly
> with `powerdown`, and magic packets were sent from bastion to **both** candidate MACs. Neither
> woke it.
>
> That narrows the cause to firmware or standby power, because everything on the OS side was
> verified before the shutdown. In order of likelihood:
>
> 1. **`ErP Ready` is Enabled** in the BIOS. It cuts +5VSB in S5 to meet standby-power limits and
>    disables WoL as a side effect. It is enabled by default on many ASUS boards of this era, and
>    it is the first thing to check.
> 2. **`Power On By PCIE Devices` is Disabled** — the wake source itself.
> 3. The smart plug was switched off during the test, leaving no standby power at all. This is
>    the one assumption the test could not verify remotely.
>
> **A five-second diagnostic that separates these:** with vault powered off, look at the link
> LED on its network port (or the switch port it feeds). Lit means standby power is reaching the
> NIC and the link is being maintained — so the problem is the wake source, item 2. Dark means
> no standby power — item 1 or 3, and no amount of OS configuration will help.
>
> The `ethtool` lines are left in `/boot/config/go`; they cost nothing and mean a retry after a
> BIOS change needs only the shutdown and the packet. **Until then the smart plug stays.**
>
> One thing the test did establish: the MAC to target is genuinely ambiguous. `eth1` carries the
> cable but is programmed with the *bond's* address (`e0:cb:4e:9d:02:6a`, which is eth0's
> permanent one) while its own permanent address is `e0:cb:4e:80:92:3d`. The `sky2` driver
> programs the wake filter from the interface's current address at suspend, so `fc:c2` is the
> more likely target — but since nothing woke, that remains untested. Send both on any retry.

The rest of this document is the original plan, kept because it is still the shape of the work
if the BIOS turns out to be the blocker.

## The question

vault is powered by a smart plug on a fixed schedule (2:00am–5:00am Sunday), with the
motherboard set to boot on power restore. Can the plug go away, with bastion waking vault by
magic packet and shutting it down over SSH instead?

## What is already established

Measured on the machine, not assumed:

| Fact | Evidence |
|---|---|
| The NICs can do magic-packet wake | Both Marvell 88E8056 (`sky2`) report `Supports Wake-on: pg` |
| They can signal from full soft-off | `PME(D0+,D1+,D2+,D3hot+,D3cold+)` — **D3cold** is the S5 standby state |
| It is off at every layer today | `Wake-on: d`, `PME-Enable-`, sysfs `power/wakeup = disabled` |
| Nothing would persist a change | Unraid rebuilds its OS in RAM each boot |
| bastion can already shut it down | `ssh root@192.168.0.212` works from bastion with `id_ed25519` |

**And the trap that would waste an afternoon:** the interfaces are bonded active-backup, so
`ip link` reports `e0:cb:4e:9d:02:6a` for *every* interface — that is eth0's address, and eth0
has no cable in it. The live link is **eth1**, whose permanent address is
**`e0:cb:4e:80:92:3d`**, and only `ethtool -P eth1` reveals it. A magic packet sent to the
address you would naturally read off the box goes to a NIC with nothing plugged into it.

## The two things that decide it

**1. Does the board hold standby power to the NIC in S5?** This is the whole question. WoL needs
+5VSB reaching the NIC while the machine is off. The plug currently cuts mains entirely, so
today the answer is moot — nothing can listen. With the plug left on, it depends on firmware.

On this board (ASUS P6X58D Premium, BIOS 1501, 2011) two settings matter, both under
*Advanced → APM Configuration*:

- **`Power On By PCIE Devices` → Enabled.** This is the wake source itself.
- **`ErP Ready` → Disabled.** ErP/EuP compliance cuts +5VSB in S5 to meet standby-power limits,
  which disables WoL as a side effect. If this is enabled, nothing else matters.

Neither can be read from the OS, so this needs a reboot into setup. A hint that they are not
currently armed: `/proc/acpi/wakeup` lists only USB wake sources, nothing for the PCIe slots.

**2. Is the standby draw acceptable?** Leaving the PSU on standby all week costs a few watts
continuously instead of ~0. Against that, the plug's 165 hours of hard-off per week is most of
the saving, and it is the reason the plug exists. If the plug was chosen for power rather than
for scheduling, this trade may not be worth making.

## What would change

Today the plug is the scheduler. Afterwards, bastion is:

| Time | Now | After |
|---|---|---|
| 2:00am Sun | plug switches on; board boots on power restore | `wake-vault.sh` on bastion sends the magic packet |
| 2:30 / 3:00 | backups run | unchanged |
| 3:30 | `vault-report.py` | unchanged |
| ~4:00am Sun | — | bastion runs `ssh root@vault powerdown` |
| 5:00am Sun | plug switches off | — |

`powerdown` is Unraid's clean shutdown: it stops the array first, so the next boot does not start
a parity check. That is already how vault was last shut down.

The real prize is not removing a plug. It is that **the backup window stops being fixed** —
bastion could wake vault whenever a backup is actually due, and vault could join wake-on-deploy
like forge and cinema.

## Steps (vault must be powered on)

1. **BIOS:** reboot into setup, set `Power On By PCIE Devices` = Enabled and `ErP Ready` =
   Disabled. Leave `Restore on AC Power Loss` alone — harmless, and it keeps the old behaviour
   working as a fallback.
2. **Arm the NIC:** `ethtool -s eth1 wol g`, then confirm with
   `ethtool eth1 | grep Wake-on` → `Wake-on: g`.
3. **Persist it.** Unraid's OS is rebuilt in RAM at every boot, so add to `/boot/config/go`:

   ```sh
   # Arm Wake-on-LAN on the cabled NIC. eth1's permanent MAC is e0:cb:4e:80:92:3d;
   # the bond makes every interface report eth0's address, which has no cable.
   /usr/sbin/ethtool -s eth1 wol g
   ```
4. **Add `bastion/scripts/wake-vault.sh`,** modelled on `wake-forge.sh` — send the packet, then
   poll for the host answering rather than assuming the packet worked. That is the bug that made
   the forge backup fail silently every week for four months; do not reintroduce it here.
5. **Add the shutdown job** to `bastion/crontab` at ~4:00am Sunday, and move the wake to 2:00am.
6. **Leave the plug switched on** but keep it installed for a few weeks, so reverting is one
   schedule change rather than a shopping trip.

## The test that decides it

With vault running and step 2 applied:

```bash
ssh root@192.168.0.212 /usr/local/sbin/powerdown     # clean stop
# wait for it to go dark, then from bastion:
ssh robby@192.168.0.10 "wakeonlan e0:cb:4e:80:92:3d || \
  python3 -c \"import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);\
s.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1);\
s.sendto(bytes.fromhex('ff'*6+'e0cb4e80923d'*16),('192.168.0.255',9))\""
ping -c1 192.168.0.212    # within ~60s if it worked
```

If it does not come up, the BIOS settings in step 1 are the first suspect, and `ErP Ready` the
likeliest culprit.

## Risk, and what already catches it

The failure mode is that WoL silently stops working — a BIOS reset, a firmware update, a moved
cable — and vault never wakes, so backups stop without anyone noticing. That is exactly the
class of failure that went unnoticed from May to September 2026.

It is already covered: `bastion/scripts/vault-report.py` runs at 3:30am Sunday whether or not
vault is up, and reports `status: error` when it cannot be reached — which the digester
escalates. **This monitoring is a precondition for making the change, not an afterthought**;
without it, swapping a dumb plug for a wake that can fail quietly would be a downgrade.

## Rollback

Put the plug back on its schedule and remove the wake/shutdown cron entries. The board still
boots on power restore, so nothing else needs undoing. Leaving the `ethtool` line in
`/boot/config/go` is harmless either way.
