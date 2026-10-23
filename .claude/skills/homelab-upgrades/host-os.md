# Host operating systems

The Ubuntu hosts are not packages and nothing in this repo manages them; `apt` on the host is the
source of truth. `SKILL.md` has the container side.

The three Ubuntu hosts are not in the table above, but they age the same way and nothing here
manages them. `unattended-upgrades` installs **security** updates on every host and never
reboots, so two things accumulate: ordinary package updates, and a pending kernel that only a
reboot activates. `apt` on the host is the source of truth, never this file.

Check, on each host:

```bash
apt list --upgradable 2>/dev/null | grep -c upgradable
[[ -e /var/run/reboot-required ]] && cat /var/run/reboot-required.pkgs
```

Apply monthly, or when a reboot is pending, one host at a time, with the script every host
carries (it re-runs itself under `sudo`, logs to `~/logs/os-update.log`, and asks before
rebooting; `--dry-run` shows what it would do):

```bash
/home/robby/homelab-deployed/<host>/scripts/os-update.sh --dry-run
/home/robby/homelab-deployed/<host>/scripts/os-update.sh
```

Order by blast radius: **forge** first (nothing depends on it and it reboots daily anyway), then
**cinema** (Plex only), then **bastion** last and in a quiet window -- its reboot takes DNS and
the vault away for about a minute, and every client pointed at AdGuard has no DNS until it is
back. The script needs a password on every host, so this is done by a person, never by the
agent. After bastion returns: `docker ps` shows its three containers up and
`dig @192.168.0.10 vault.robbywhite.net` answers.

**forge, after an NVIDIA driver update:** ollama takes the GPU through CDI, from a spec file
that describes the installed driver. A new driver makes that file stale and ollama will not
start until it is regenerated, so run this before `docker compose up` or any deploy:

```bash
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml && nvidia-ctk cdi list
```

`os-update.sh` prints a reminder on forge when the upgrade touched an `nvidia-` package. Why CDI
and not the legacy hook is in the comment above the `devices:` line in `forge/docker-compose.yaml`.

**forge, the driver must load at boot by itself.** CDI injects devices but, unlike the old
runtime hook, never loads kernel modules. Two host-side settings do that and must survive
any driver or kernel work: `prime-select query` must say `nvidia` (the `intel` profile writes
`/lib/modprobe.d/blacklist-nvidia.conf`, which is exactly what left the GPU driverless on
2026-10-10), and `/etc/modules-load.d/nvidia.conf` lists `nvidia` and `nvidia_uvm`. After a
reboot: `nvidia-smi -L` on the host, then `docker exec ollama nvidia-smi -L`; if the first
fails, `sudo prime-select nvidia && sudo modprobe nvidia nvidia_uvm` and regenerate the CDI
spec as above.
