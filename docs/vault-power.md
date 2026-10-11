# vault's power: Wake-on-LAN instead of a smart plug

> **Revision 2026-10-11 — the plug's schedule is back.** WoL from bastion worked exactly once
> (2026-09-27, after a 16-hour sleep) and failed every time vault had been off for days
> (2026-10-04, 2026-10-11, and a manual attempt the same morning), with every setting below
> still correct — the board simply does not wake from a long S5, and Robby does not want an
> X58 board idling all week. So the Tapo P115 goes back inline with vault's PSU with its own
> schedule: **ON Sunday 01:55, OFF Sunday 05:00**. The board boots on power restore; the 02:00
> `wake-vault.sh` finds it booting and its WoL packet is a harmless no-op; `sleep-vault.sh` at
> 04:00 still shuts Unraid down cleanly before the plug cuts power an hour later. The plug sits on
> `WhiteHouse_IoT` (isolated — the schedule runs on the plug itself through Tapo's cloud, bastion
> never needs to reach it) and is pinned as `vault-plug`, .173. Trade-off accepted: an on-demand
> wake on another day means switching the plug on in the Tapo app, or pressing the button. If
> `sleep-vault` ever fails to shut vault down, the 05:00 cut is an unclean power-off and Unraid
> will run a parity check on the next boot — the `backup` health check reports the failed sleep.
> Everything below stays true and stays configured; it is the fallback, not the mechanism.
>
> *Same day, later:* the boot times of bastion and cinema show house-wide power outages at
> **Sun 10-04 21:17** and **Sat 10-10 04:18**, and Robby reports outages both Saturday nights.
> Every WoL failure on forge in those two weeks followed one of them and ended with a button
> press; the only vault wake that ever worked (09-27) had no outage before it. An AC loss
> clears the standby-domain state a magic packet relies on, so neither board's WoL was broken
> — both were waiting for a boot that only a button or a power cycle gives. The plug gives
> vault that power cycle every Sunday regardless; forge gets the BIOS setting *restore on AC
> power loss → Power On* so it boots itself after an outage and re-arms at its next 23:00 shutdown.
> **Confirmed the same morning:** one minute after a clean, armed `sleep-vault`, a magic packet
> woke vault in 102 s (ping), array started at 143 s; the full Sunday chain then ran by hand
> (backup-to-tower, sync-forge, vault-report, sleep-vault), all ok. WoL works; outages are what
> break it. The plug schedule stays as the mechanism because it survives outages and Robby wants
> the board hard-off all week; WoL remains the on-demand path on a vault that was shut down cleanly.
> Robby confirmed vault's BIOS APM setting as *Power On* after AC loss the same day.

**Done, 2026-09-26.** bastion wakes vault with a magic packet and shuts it down over SSH. The
smart plug stays installed and switched on as a manual override, but nothing schedules it.

## The root cause, which took four failed tests to find

Wake-on-LAN needs **three** things to be true on this board, and only the first is the one
everybody talks about:

| # | Requirement | How to set it | Symptom if missing |
|---|---|---|---|
| 1 | The NIC is armed for magic packets | `ethtool -s ethN wol g` | `Wake-on: d` — obvious |
| 2 | **The NIC's PCIe root port may raise a wake** | `echo P0PN > /proc/acpi/wakeup` | **Nothing. Everything looks right.** |
| 3 | The kernel's per-device wake flag is set | set by `ethtool`, visible in sysfs | as #2 |

Requirement 2 is the one that cost the time. The NICs sit behind ICH10 root ports:

```
eth1 (05:00.0) <- 00:1c.5 = ACPI P0P9
eth0 (06:00.0) <- 00:1c.2 = ACPI P0P6
```

Both were `*disabled` in `/proc/acpi/wakeup`. PME from the card has to travel up through that
bridge to reach the chipset, so the wake event was being raised and then dropped one hop later.
Every observable signal said the configuration was correct: `Wake-on: g` on the card, the BIOS
option enabled, standby power present (link LED lit), the packet confirmed sent to the right MAC.
It simply never woke.

**Lesson for the next time something like this happens:** four separate tests each produced a
clean negative, and each one tempted a different wrong conclusion — a too-short poll window, then
a coincidental boot mistaken for a late wake, then "the board can't do it", then "maybe only one
NIC supports it". None of those were it. What actually found it was enumerating the whole causal
chain — card, bridge, chipset, firmware, standby power — and checking each link, instead of
testing variations on the last thing tried.

A third bug, caught by testing rather than in production: `wol-arm.sh` originally ended with a
`grep && echo` loop, which **returns non-zero when everything is already armed** — the normal
case. `sleep-vault.sh` reads a non-zero exit as "could not arm" and refuses to power vault down,
so that one missing `exit 0` would have meant vault never shut down again, every Sunday, quietly.
It failed safe and would have failed forever.

A secondary bug, found along the way: **the boot-time arming was unreliable.** Unraid brings the
bond up *after* `/boot/config/go` runs and resets the NIC's WoL bit, so a single `ethtool` call
there won the race on some boots and lost it on others — both were observed, which is worse than
consistently failing.

## How it is configured

**On vault** — both files on the flash, because Unraid rebuilds its OS in RAM at every boot and
nothing under `/` survives:

- `/boot/config/wol-arm.sh` — idempotent, handles all three requirements above. Versioned in
  this repo at [vault/wol-arm.sh](../vault/wol-arm.sh) and installed by hand, since vault is not
  a deploy target and that flash is a USB stick with no other backup.
- `/boot/config/go` — runs `wol-arm.sh` every 30s for the first five minutes, rather than once,
  to beat the bond race. The previous `go` files are backed up alongside it.

**On bastion** — the scheduler, replacing the plug:

| Time (Sunday) | Job |
|---|---|
| 2:00am | [wake-vault.sh](../bastion/scripts/wake-vault.sh) |
| 2:30am | `backup-to-tower.sh` |
| 3:00am | `sync-forge.sh` |
| 3:30am | `vault-report.py` |
| 4:00am | [sleep-vault.sh](../bastion/scripts/sleep-vault.sh) |

`wake-vault.sh` waits on **three** gates, not one: ping, then SSH, then `mdState=STARTED`. The
third matters most and is the easiest to skip — Unraid's root filesystem is RAM, and
`rsync --mkpath` would happily create `/mnt/user/backups` on it and report success, producing a
backup that evaporates at the next boot. A backup that lies is worse than one that fails.

`sleep-vault.sh` arms WoL immediately before powering down rather than trusting boot-time state,
and **refuses to power vault down if it cannot verify the arming.** A vault that is off and
unwakeable needs someone to walk over to it; a vault that is still running costs a few watts.
Those are not symmetrical failures, so it fails towards the recoverable one.

The agent also has a `wake-vault` verb on bastion, so vault can be woken on demand without
waiting for Sunday.

## What this buys

- **The backup window is no longer fixed.** bastion can wake vault whenever a backup is due,
  rather than the schedule having to be timed around a plug.
- **No dependency on the plug's clock** — or on remembering that its schedule fires on
  *transitions*, so leaving it switched on manually silently skipped a whole window.
- vault can be woken on demand, from cron or from the agent.

## Failure modes

| Failure | Caught by |
|---|---|
| WoL stops working (BIOS reset, firmware update, cable moved) | `wake-vault.sh` logs it and exits non-zero; `vault-report.py` reports `status: error` at 3:30am and the digester escalates |
| vault wakes but the array does not mount | `wake-vault.sh` gate 3 refuses to let the backups run |
| vault cannot be armed, so shutting it down would strand it | `sleep-vault.sh` refuses to power it down |
| Backups silently stop arriving | `vault-report.py` reports `backup_age_days`, and the digester escalates past 8 days |

**This monitoring was a precondition, not an afterthought.** Swapping a dumb plug for a wake that
can fail quietly would have been a downgrade without it.

## Costs and the fallback

Standby power now runs continuously — a few watts, against the plug's 165 hours a week of hard
off. If that matters more than the flexibility, the plug's schedule can simply be re-enabled: the
board still boots on power restore, and the wake/sleep cron entries become harmless no-ops
(`wake-vault.sh` exits early when vault is already up). Leaving the `ethtool` and
`/proc/acpi/wakeup` configuration in place costs nothing either way.

## If it ever needs re-testing

```bash
# from bastion, with vault running
/home/robby/homelab-deployed/bastion/scripts/sleep-vault.sh   # arms, verifies, powers off
/home/robby/homelab-deployed/bastion/scripts/wake-vault.sh    # wakes, waits for the array
tail -20 /home/robby/logs/power.log
```

Expect roughly 60–90 seconds from packet to first ping on this board, and a further few seconds
for the array. Anything under two minutes is normal; a poll window of one minute is not enough,
which is how the first test went wrong.
