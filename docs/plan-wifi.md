# Plan: WiFi — two controllers on bastion, an IoT radio by the sauna, and client logging

Written 2026-10-07, the evening the sauna controller lost its second scheduled session. The
chain of evidence is in [plan-kids-hours-and-iot.md](plan-kids-hours-and-iot.md): the HUUM
controller sits at -74 dBm on the EAP772's 2.4 GHz radio, drops off the WiFi several times a
day, and wipes its armed start every time it reconnects to its cloud. The EAP772 in standalone
mode logs none of that — its syslog (now pointed at bastion) carries boot lines only — and
there is no second radio nearer the sauna.

Decisions taken with Robby the same evening:

- **Keep the EAP772** (WiFi 7, fast) as the main access point for the house.
- **Put the UniFi AC Lite near the sauna** as a 2.4 GHz-only radio for the IoT devices. The
  AC Pro waits for an 802.3af injector (the one on hand is 24 V passive, Lite-only).
- **Run both vendors' controllers on bastion**, each in Docker: Omada for the EAP772, UniFi
  Network for the AC Lite (and the Pro later). Two controllers is the price of client-level
  logging for every wireless device, and it is cheaper than replacing either side.

Same marking as the other plans: **[agent]** needs no password; **[you]** needs sudo, an AP in
your hand, or a device to move.

## What it gives

| Today | After |
|---|---|
| EAP772 standalone; settings on its own page; no client history | EAP772 managed by Omada on bastion: SSIDs, scheduler, channels, power in one place, with client join/leave history and signal over time |
| One radio for the whole house; the sauna controller at -74 dBm | AC Lite by the sauna broadcasting a 2.4 GHz IoT SSID; the HUUM controller at a usable signal; plugs, printer and Espressif boards can follow |
| "Which device is that?" answered from AdGuard's lease table alone | Both controllers' client lists feeding `lan_clients`, so the Network dashboard shows every device with its group, radio, band and signal — the audit asked for on 2026-10-07 |
| IoT devices on the same SSID as the laptops | An IoT SSID with client isolation: option B of [plan-lan-groups.md](plan-lan-groups.md), now with hardware to put it on |

## Components

| Component | Image | Runs | Needs |
|---|---|---|---|
| Omada SDN controller | `mbentley/omada-controller` (bundles MongoDB) | bastion, host network | ~1.5 GB RAM; a volume for `/opt/tplink/EAPController/data` and `/logs` |
| UniFi Network application | `linuxserver/unifi-network-application` | bastion, host network | ~1 GB RAM; a volume for `/config`; an external MongoDB |
| MongoDB for UniFi | `mongo` (a version UniFi supports; check the linuxserver README at build time) | bastion, bridge, never published | a volume; an init script creating the `unifi` user |

Tags are pinned at build time from each project's current release and get package files in
the upgrades skill (Watch tier: both hold AP admin credentials and a login on the vault host).

## Networking on bastion

Both controllers assume host networking because their APs find them on fixed ports. That
means ports to reconcile and firewall rules to add.

**Collisions to resolve before either starts**

- Both controllers use **8843** for their captive portal. Omada's moves (`PORTAL_HTTPS_PORT`).
- UniFi's AP inform port is **8080**, which Vaultwarden's loopback publish holds today. That
  publish is only there for `curl` on the host (Caddy reaches Vaultwarden by service name), so
  it moves to `127.0.0.1:8081`; the README's table is updated with it.
- Omada's `MANAGE_HTTP_PORT` default 8088 and UniFi's 8880 do not collide with anything.

**ufw on bastion, from the LAN only [you]**

| Port | Proto | Who | For |
|---|---|---|---|
| 8043 | tcp | 192.168.0.0/24 | Omada UI (also via Caddy) |
| 29810 | udp | 192.168.0.0/24 | Omada discovery |
| 29811–29816 | tcp | 192.168.0.0/24 | Omada management and adoption |
| 8443 | tcp | 192.168.0.0/24 | UniFi UI (also via Caddy) |
| 8080 | tcp | 192.168.0.0/24 | UniFi inform |
| 3478 | udp | 192.168.0.0/24 | UniFi STUN |
| 10001 | udp | 192.168.0.0/24 | UniFi discovery |

`ufw allow from 192.168.0.0/24 to any port <p> proto <tcp|udp>` for each. Container-to-host
traffic from the `172.16.0.0/12` side needs nothing new: Caddy reaches both UIs on the host
address through the existing 443/9100/9200-style rule pattern, one more rule for 8043 and 8443.

**Names**

- AdGuard rewrites: `omada` → `192.168.0.10` (the EAP772 already asks for it every ten
  seconds) and `unifi` → `192.168.0.10` (the UniFi AP's default inform host).
- Caddy, LAN-only with `tls internal` exactly like `grafana.robbywhite.net`:
  `omada.robbywhite.net` → `https://192.168.0.10:8043` and `unifi.robbywhite.net` →
  `https://192.168.0.10:8443` (the host's LAN address, as for AdGuard: `127.0.0.1` inside the
  Caddy container is the container), both with `transport http { tls_insecure_skip_verify }`
  because the controllers present their own self-signed certificates.

## The IoT SSID

- Name and password: **[you]** to choose; recorded nowhere in git.
- 2.4 GHz only, on the AC Lite first; later on the AC Pro too if it ever carries IoT elsewhere.
- **Client isolation on**: devices on it reach the internet and nothing on the LAN. That is
  the right setting for the HUUM controller, the plugs, the printer and the Espressif boards.
  **Not** for the Echos if multi-room audio is used, and **not** for the ecobee while it is
  paired with HomeKit — those stay on the main SSID, as the LAN-groups plan says.
- Fixed channel (1, 6 or 11, whichever the controller's scan shows emptiest), 20 MHz width,
  no band steering (there is nothing to steer to).
- **The EAP772's 2.4 GHz radio stays on** (decided 2026-10-07): the Echos, the ecobee and the
  Apple TVs' HomeKit traffic are 2.4 GHz devices that stay on the main SSID, and the central
  cell is where phones fall back to at the edges of the house. The two 2.4 GHz radios take
  different fixed channels from {1, 6, 11}, both at 20 MHz, so they do not overlap.

## Phases

### Phase 1 — controllers up, nothing adopted [agent + you], one evening

Started 2026-10-09, the evening the AC Lite was plugged in (it took lease `192.168.0.136`,
MAC `80:2a:a8:78:18:6a`, and is asking DNS for `unifi`).

1. **[agent]** ✅ `bastion/wifi/docker-compose.yml` with the three services, volumes, pinned
   tags (Omada `6.2.14.11` — v6 needs AVX for its bundled MongoDB 8, which bastion's i3-7100T
   has; UniFi `10.6.106-ls149`; MongoDB `7.0.43`), Omada's portal port moved to 8844;
   `bastion/apply.sh` validates and brings up the third compose project and requires
   `UNIFI_MONGO_ROOT_PASSWORD`/`UNIFI_MONGO_PASSWORD` in `.env`; Vaultwarden's loopback
   publish moved to 8081; Caddy vhosts; AdGuard rewrites through the API; package files in
   the upgrades skill; `backup-observability.sh` archives both autobackup directories.
   UniFi's MongoDB sits on a bridge network with a `127.0.0.1:27017` publish and UniFi (host
   network) connects to it over loopback, so the database is reachable from bastion only.
2. **[you]** The seven ufw rules above, plus `8043,8443` from `172.16.0.0/12`.
3. **[agent]** Deploy. Both UIs answer on their LAN names. The EAP772 shows up in Omada as
   *pending adoption* by itself (that is the `omada` lookup); nothing is adopted yet.
4. **[you]** First-run wizards: admin accounts for both controllers. Passwords in Bitwarden,
   not in `.env` — the agent never needs to log in to either.

**Done when:** both UIs load from the laptop, the EAP772 is listed as pending in Omada, and
`docker stats` on bastion shows the three containers idling.

**Done 2026-10-09/10.** ufw rules in, both wizards through (local accounts, no Ubiquiti or
TP-Link cloud login), EAP772 discovered by Omada within minutes (`NO_MANAGED Device
A8-29-48-16-DD-07 … discovered`). Idle: Omada 1.3 GB, UniFi 0.7 GB, MongoDB 76 MB (its cache
capped at 256 MB; the default would have taken half of bastion's RAM). Three things found on
the way, each now in the files that need them:

- **Caddy rewrites `Host` for `https://` upstreams** (it keeps it for plain ones), and UniFi
  refuses any POST whose `Origin` does not match `Host` — the setup wizard failed with
  "Cannot configure network server" (403 in 1 ms) until both vhosts got `header_up Host {host}`.
- **UniFi answers `/inform` with 404 until it has seen the device by L2 discovery** (UDP
  10001). The AC Lite informed every 15–90 s and was refused each time; the controller only
  listed it as pending after a restart with the 10001 rule already in place. If an AP is
  informing but never appears, restart the controller before anything else.
- **The AC Lite was on firmware 4.0.80 (2019)**, which a factory reset does not change. It was
  flashed by hand to the current release (the UniFi firmware API and the controller's own
  update check agreed on the version): `scp` the `.bin` to the AP's `/tmp/fwupdate.bin`,
  `md5sum` it against the API's hash, `syswrapper.sh upgrade2 &`. The 2019 SSH stack needs
  `-o HostKeyAlgorithms=+ssh-rsa -o KexAlgorithms=+diffie-hellman-group14-sha1` from a current
  macOS; default login `ubnt`/`ubnt` until adoption replaces it.

### Phase 2 — the EAP772 under Omada [you, agent watching], 30 minutes, brief WiFi blip

1. **[you]** From the EAP772's standalone page, write down: SSID names and passwords, 2.4 and
   5 GHz channels and widths, transmit power, the scheduler profiles, the management VLAN
   (none). This is the rollback record; paste it into this file under *EAP772 record*.
2. **[you]** In Omada, create a site, then the same SSIDs with the same passwords **before**
   adopting, so the controller provisions them in the same breath as it takes over.
3. **[you]** Adopt. The AP reboots into managed mode; clients reconnect within a minute.
4. **[agent]** Checks: every device that was on WiFi is back in AdGuard's lease table; the
   kids' static leases unchanged; the sauna controller connected; the Omada client list shows
   them with signal.
5. **Rollback:** forget the AP in Omada and reset it to standalone with the record from step 1.

**Done 2026-10-10 09:03.** The first adopt failed with Omada's `errorCode 5` (`device_verify
auth … adopt info is wrong`): the EAP772 had its own admin login from standalone mode and
Omada tried the default; the retry with the device's credentials entered went through.
Within six minutes every device was back — 21 clients on DNS, all ten 2.4 GHz-only IoT
devices answering ping, the kids' static leases untouched, the HUUM controller the slowest
at five minutes (idle, nothing armed). The Omada 6 path for the SSID is *Network Config →
WLAN → Add SSID*, inside the site, not the global view. Adoption did reset the AP's syslog
target; the Loki `syslog` job is quiet until it is set again from Omada's site services, and
with the controller's own client log that is now optional. Step 4 (fixed channels and power
from the record) is done from the device's *Config → Radios* page.

### Phase 3 — the AC Lite by the sauna [you + agent], one evening

1. **[you]** Mount and cable the AC Lite as close to the sauna as a cable reaches, on the 24 V
   injector. Factory-reset it (hold reset ten seconds).
2. **[you]** It appears in UniFi as pending (via the `unifi` name). Adopt, set 5 GHz off, and
   create the IoT SSID from the section above. *Adopted and 5 GHz off on 2026-10-10, ahead
   of the mounting — it sits on the bench broadcasting nothing until the SSID exists.*
3. **[you]** Move the HUUM controller to the IoT SSID (its WiFi setup is in the HUUM app).
4. **[agent]** Watch for 24 hours: the *Cloud reconnects* panel should fall to the one-a-week
   baseline it had before the outage, and the UniFi client entry should show a signal in the
   -50s to -60s. Then move the plugs, the printer and the Espressif boards one at a time.
5. **[agent]** A static lease for each device that moved; `lan_clients` already follows.

**Done when:** a session armed on the controller starts on time three nights running.

*Steps 1–3 done 2026-10-10 by 09:45*: AC Lite mounted by the sauna, IoT SSID up (2.4 GHz
only, WPA2, client isolation, IoT Optimization on, channel 6), HUUM controller moved and
online within minutes at **-52 dBm** (was -74 on the EAP772) — ping from bastion 3.7 ms average, no loss. The 24-hour watch is the
existing `huum-reconnects` Grafana rule (>3 cloud reconnects in 6 h → #homelab-status); the
day's count before the move was 5 in the 04:00 hour alone. The remaining IoT devices move
after that watch passes, one at a time.

*Three-night test:* night 1 (2026-10-10) — armed from the handset, held all day, no connection
loss (the AT&T gateway's radios went off the same day; see phase 4).

### Phase 4 — the audit [agent, two credentials from you], one evening

- Both controllers expose an API (Omada's OpenAPI with a client credential created in its UI;
  UniFi's local API with a read-only user). `bastion/scripts/wifi-clients.py` reads both every
  five minutes and upserts `lan_clients` with SSID, band, AP, signal and uptime, next to what
  AdGuard already supplies. The read-only credentials live in bastion's `.env` like the others.
- The *Network* dashboard gains those columns and a per-device signal history; a Grafana rule
  for "an IoT device below -70 dBm for an hour" replaces guessing.
- This closes step 2 of [plan-lan-groups.md](plan-lan-groups.md): every device named, grouped,
  and pinned, now with the wireless half filled in.

**Built 2026-10-10**, deployed with the credentials still empty (the poller skips a
controller whose credential is missing, so nothing fires until they exist):

- `bastion/observability/postgres/002-wifi.sql` adds `wifi_controller, ap, ssid, band,
  signal_dbm, wifi_uptime_s, wifi_seen` to `lan_clients`; `apply.sh` now replays the schema
  files whenever one changes (all are written to be re-runnable).
- `bastion/scripts/wifi-clients.py --poll` from cron at minutes 2, 7, 12 …; `--dump` prints a
  raw client object from each controller for checking the field mapping after an upgrade.
  Omada's `rssi`/`signalLevel`, `radioId` and `apName`; UniFi's `signal`, `radio` and `ap_mac`
  resolved to the AP's name. Gauges in `wifi.prom`: `wifi_poll_ok`, `wifi_clients`,
  `wifi_client_signal_dbm`, `wifi_client_uptime_seconds`.
- *Network* dashboard: the device table shows controller, AP, SSID, band, signal (coloured at
  -70/-62) and when it was last seen on WiFi; a signal-history panel below it.
- Rules in `rules-wifi.yml`: `wifi-weak-signal` (a UniFi-side client below -70 dBm for an
  hour — every UniFi client is on the IoT SSID by design) and `wifi-poll-failed`.
- **[you]** the two credentials, into bastion's `.env` (`.env.example` has the exact clicks):
  an Omada Open API client in *Client Credentials* mode with the **Viewer** role
  (`OMADA_CLIENT_ID`, `OMADA_CLIENT_SECRET`), and a UniFi local admin restricted to local
  access with the read-only role (`UNIFI_USER`, `UNIFI_PASSWORD`). Then
  `wifi-clients.py --dump` once to confirm the mapping, and the table fills on the next run.
- **Found 2026-10-10 by the first real poll: the AT&T gateway's own WiFi was still on**, broadcasting
  `WhiteHouse` from the same room as the sauna, and most of the house had been on it — Omada
  counted 4 wireless clients while 16 WiFi-only devices answered ping, and had *never seen* the
  plugs or this laptop (`-41011`). The gateway has no controller, API, client list or per-SSID
  isolation, so it cannot be the IoT radio; Robby turned both its bands off and 25 clients
  landed on the EAP772 within a minute (14 × 2.4 GHz, 9 × 5 GHz, 2 × 6 GHz). Every earlier
  "all devices back" check in this file was DNS- and ping-based and could not tell the radios
  apart; the controllers can. This also explains part of the HUUM's history: a device within
  reach of two radios named the same thing flaps between them.
- First signal map, same poll: the two 6 GHz clients (the Mac and an iPad) sit at -79/-81 dBm,
  worse than they would on 5 GHz — band steering is pushing them to a band that does not
  reach; worth a look at the EAP772's 6 GHz power or steering thresholds. The 2.4 GHz plugs
  range from -47 (hs200) to -78 (a Tapo); the Epson printer is wired.
- The "device on the wrong AP for its group" rule waits for the groups themselves (step 2 of
  the LAN-groups plan puts the group on the row); with the SSIDs split by vendor it cannot
  happen today anyway.

### Phase 5 — an IoT-home SSID on the EAP772, then the kids' SSIDs [you + agent]

Added 2026-10-10. Two SSID changes on the EAP772, in this order because the second forces a
`WhiteHouse` password rotation and the first keeps that rotation small.

1. **`<home-iot>` SSID on the EAP772 [you]** — Omada, *Network Config → WLAN → Add SSID*:
   2.4 GHz only, WPA2, **no** client isolation (these devices need the LAN), 802.11ax off
   and band steering off for the SSID. Move the four Echos, the ecobee and the Haiku fan to it
   (each from its own app). The Apple TVs stay on `WhiteHouse`: they stream on 5 GHz. After
   this, `WhiteHouse` carries only people's devices and the Apple TVs, and the LAN-groups
   `iot-amazon`/`iot-home` members are on an SSID of their own without losing LAN access.
   **[agent]** static leases for each as it moves, in the `iot-*` ranges.
2. **`Ava` and `Finn` SSIDs on the EAP772 [you]** — all bands, WPA2/WPA3, each with an
   Omada SSID schedule matching the child's window in
   [plan-kids-hours-and-iot.md](plan-kids-hours-and-iot.md) (Ava off 22:00/23:00 → on 05:30;
   Finn off 21:30/22:15 → on 06:00). Their devices join their own SSID.
3. **Rotate `WhiteHouse` [you]** and re-join the family's phones, laptops and the Apple TVs.
   The kids do not get the new password; without this step the SSID schedule is advice.
4. **`kids-hours.py` drives the SSIDs [agent]** — a second Omada Open API credential with the
   Operator role (the Viewer one from phase 4 cannot change anything), stored separately in
   `.env`, lets the script enable/disable each child's SSID alongside its DNS rule, so the
   Slack verbs (`kids-open-ava`, `--until`) and the overrides keep working as one system.
   Until then an override is the DNS rule only, and the SSID schedule is edited in Omada.
5. What the DNS layer still covers that the SSID cannot: a wired device, and anything a
   child joins to another network. Both layers stay.

## Risks, named

- **Two more always-on services on the vault host**, both Java, both holding AP admin
  credentials. Mitigated the same way as Grafana: LAN-only, own logins, never published to
  the internet, backed up. The APs keep working if either controller is down; only changes
  and logging stop.
- **Adoption resets the EAP772's configuration.** Hence the record in phase 2 and creating the
  SSIDs before adopting. Expect one minute of WiFi loss for every device in the house.
- **Mixed vendors on one SSID name** is fine for roaming in practice but offers no
  coordination; the design avoids it by giving the AC Lite only the IoT SSID.
- **MongoDB versions.** UniFi is particular about which MongoDB it accepts; the linuxserver
  README's table decides the tag, and the upgrades package file for UniFi records that the
  two move together.

## Backups

Both controllers write nightly autobackups into their volumes (Omada: `data/autobackup`;
UniFi: `config/data/backup/autobackup`). `backup-observability.sh` grows two lines to archive
those directories into `~/backups/`, which already mirrors to vault on Sunday.

## Revision 2026-10-10 — a second EAP772 replaces the AC Lite

The first signal map showed the office laptops at -79/-81 dBm on 6 GHz under an AP one floor
up, and the gateway finding showed what two vendors cost in visibility. The utility closet
downstairs shares a wall with both the office and the sauna bathroom, so one more EAP772
there (ordered 2026-10-10, with its DC adapter — the 24 V passive injector cannot power it)
gives the laptops a close 5/6 GHz cell, the HUUM a close 2.4 GHz cell, and Omada-managed
roaming between the two APs. The AC Lite, the UniFi application and its MongoDB then go.

What changes, phase by phase:

- **Phase 1** — the `unifi` and `unifi-db` services, their volumes, Caddy vhost, AdGuard
  rewrites (`unifi`, `unifi.robbywhite.net`), `.env` entries (`UNIFI_MONGO_*`, `UNIFI_USER`,
  `UNIFI_PASSWORD`), ufw rules (8443, 8080, 3478/udp, 10001/udp, and 8443 from
  172.16.0.0/12), the `packages/unifi.md` runbook and the `unifi-*` backup line are removed
  once the new AP carries the IoT SSID. Vaultwarden's loopback publish stays on 8081; there
  is no reason to move it back. The still-unfinished UniFi read-only admin is no longer
  needed.
- **Phase 3** — the IoT SSID becomes an Omada WLAN: `WhiteHouse_IoT`, 2.4 GHz only, WPA2,
  **Guest Network on** (Omada's client isolation), 802.11ax off, broadcast from **both APs**
  (decided 2026-10-10: one isolated SSID rather than one per AP — two APs on one controller
  coordinate, and a fixed device simply keeps the AP it hears best; a per-AP SSID is the
  fallback for any single device that ever flaps). The HUUM moves to it (same name and
  password as the UniFi one, so it may simply follow). Done when the controller shows it in
  the -50s on the closet AP and the three-night test passes.
- **Phase 4** — `wifi-clients.py` loses its UniFi half; the `wifi-weak-signal` rule selects
  on `ssid="WhiteHouse_IoT"` instead of `controller="unifi"`. Everything else stays.
- **Phase 5** — unchanged; both kids' SSIDs and the home-IoT SSID are Omada WLANs on both
  APs, so a device keeps its SSID when it roams between floors.
- **Channel plan** — Omada picks per-AP 2.4 GHz channels itself with two of its own APs
  (1 and 6 or 1 and 11); the "lock to 6" setting was UniFi's and goes with it.

Order when the AP arrives: mount it (inside the closet if the wall is drywall, on the
office side of that wall if the closet is lined with metal or ducting), cable it to the
closet switch, power it from the adapter. It appears in Omada as pending, adopts with the
site's SSIDs, and the office laptops should land on it within minutes. Then the IoT WLAN,
the HUUM move, and the UniFi retirement in that order — the AC Lite keeps the sauna working
until the new AP has proven it.

## Which device goes where

The per-device answer — SSID, AP, fixed address, what the thing is — lives in
[lan-clients.md](lan-clients.md), the client register started 2026-10-10 when the first
signal map made it possible to decide. Phases 3 and 5 move devices according to that file.

## Client isolation — the note for later

The IoT SSID has client isolation on: devices on it reach the internet and nothing on the
LAN. Today that costs nothing, because every device that moves there is controlled through
its vendor's cloud anyway (Kasa, Tapo, the Amazon plug, HUUM) or needs no control (printer).
**If Home Assistant, or anything else that talks to these devices by LAN address, is ever
added, this is the setting to revisit.** Two ways out, in order of preference: keep isolation
and let the integration use the vendor cloud (Tapo and Kasa both have cloud integrations);
or switch isolation off on the SSID — one toggle in UniFi → Settings → WiFi → the IoT
network → Advanced — which keeps the SSID, the radio and the channel plan and only drops the
LAN barrier. Devices that need the LAN for their own apps or for HomeKit never go on the IoT
SSID in the first place: the Echos, the ecobee, the Apple TVs, the Haiku fan.

## Not doing

- Replacing the EAP772 with UniFi, or the UniFi APs with Omada. Both ecosystems stay; the
  controllers are the bridge.
- VLANs. Still option C of the LAN-groups plan and still needs a router that does them.
- Running either controller on forge or cinema. They sleep.

## EAP772 record

Taken from the standalone page on 2026-10-10, before adoption. This is the rollback: forget
the AP in Omada, reset it to standalone, and put these back.

- **IP** 192.168.0.2 (AdGuard static lease, MAC a8:29:48:16:dd:07); management VLAN none.
- **SSID** `WhiteHouse` on all three bands, one password (Bitwarden). Security type not
  noted on the standalone page — read it off the SSID's edit dialog when recreating it in
  Omada (expect WPA2/WPA3-Personal mixed; 6 GHz requires WPA3).
- **2.4 GHz** enabled, channel **6** (2437 MHz), **20 MHz**, b/g/n/ax/be mixed, TX 25 dBm.
- **5 GHz** enabled, channel **40** (5200 MHz), **80 MHz**, a/n/ac/ax/be mixed, TX 25 dBm.
- **6 GHz** enabled, channel **69** (6295 MHz), **320 MHz**, ax/be mixed, TX 23 dBm.
- No scheduler profiles. Nothing else changed from defaults (mesh, band steering, LED).

Channel plan, settled 2026-10-10 during phase 3: UniFi's *IoT Optimization* panel locks all
its APs' 2.4 GHz radios to **channel 6**, so the **EAP772 moved to channel 1** (20 MHz) and
the AC Lite (and the AC Pro later) sit on 6. The record above shows the pre-adoption value;
this is the deliberate change. The IoT SSID also has *Force WiFi 4 Mode* and *DTIM Interval
Lock* on, WPA2, client isolation, 2.4 GHz only.
