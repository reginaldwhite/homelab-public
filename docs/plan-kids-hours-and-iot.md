# Plan: children's internet hours, and logging the sauna, thermostat and sprinklers

Two new features, planned 2026-09-27 on top of the state left by
[plan-2026-09-27.md](plan-2026-09-27.md): AdGuard is now the LAN's only DHCP and DNS server,
the router's DHCP is off, WiFi is a standalone TP-Link EAP772 at `192.168.0.2`, and every host is
patched. Decisions taken with Robby the same day are marked **decided**; the rest are the
recommendation, with the alternative and why it lost.

Same marking as the other plan: **[agent]** needs no password; **[you]** needs a password, an
app, or a device in your hand.

---

## Part 1 — internet hours for the children

### Decided

- **Scope: all internet**, not just entertainment. Off-hours, a child's device can reach the LAN
  and nothing else.
- **Two windows, one per child** (revised 2026-09-28 from a single 07:00–20:00 window):
  Ava (15) every night 05:30–23:30 (from 2026-10-25; was Sun–Thu 22:00, Fri–Sat 23:00);
  Finn (13) Sun–Thu 06:00–21:30, Fri–Sat 06:00–22:15. One place holds them (below); changing it there changes every layer.
- **Plex is off with the rest, probably.** The children's block carries `important`, so it
  outranks the `@@||plex.direct^` allow rule (which lost its own `$important` for this). But
  AdGuard answers DNS *rewrites* before it evaluates rules, so `*.plex.direct` names still
  resolve for a blocked device; what the block removes is `plex.tv`, which the Plex app needs to
  sign in and find the server. Whether the Roku's Plex app still plays at 21:35 is the test; if
  it does, the rewrite has to go (Plex publishes those names publicly anyway).
- **Nothing blocked always-on yet**; revisit with a week of the query log.
- **Devices, by child:** Finn — the main PC (`finn-pc`, .150), the Chromebook
  (`finn-chromebook`, .151, school-managed), the playroom Roku (`finn-roku`, .152, the Plex
  client), the iPad named `robbys-ipad` (`finn-ipad`, .153) and his phone (`finn-phone`, .154).
  Ava — her phone (`ava-phone`, .155) and the iPad mini (`ava-ipad-mini`, .156, still to be
  pinned: candidates `.120` and `.122`). `robby's ipad (2)` is yours.

### How enforcement works, and why three layers

There is no single box in this network that both sees every packet and takes a schedule.
bastion is not in the traffic path, the EAP only sees WiFi, and the BGW320 only takes a
schedule through AT&T's cloud app. So the design is layered, each layer catching what the
one above misses:

| Layer | Where | Enforces | Misses | Why it is here |
|---|---|---|---|---|
| **1. Gateway profile** | AT&T Smart Home Manager → *Profiles* → time limits | every packet, wired and WiFi, by MAC | nothing on the LAN side; depends on AT&T's app and cloud | the only layer a device cannot dodge by changing its own settings |
| **2. AdGuard** | bastion, per-client rules on a cron | all DNS for those clients off-hours | a device given a hard-coded DNS server or DNS-over-HTTPS | self-contained, and the **only layer that logs**: every blocked attempt is in the query log and the morning digest |
| **3. The device** | Screen Time (Apple), Family Link (Chromebook), Roku PIN | the device itself, anywhere | anything the child can reset | keeps a phone honest on cellular and at a friend's house, which no network layer can |

If layer 1 turns out not to work on this gateway now that it is no longer the DHCP server
(untested; the app identifies devices by MAC from the gateway's own table, which still sees
every device), the fallback is the **EAP772's scheduler**: a second SSID for the children with
*Radio Off* outside the window (Wireless → Scheduler, *Associated with SSID*, up to eight
profiles). That covers every WiFi device but not the wired PC, which would then need layer 1 or
a smart plug on its switch port. It is the fallback rather than the plan because it forces the
children onto their own SSID and the main password out of their hands, which is more to manage.

### The one place the windows live

`bastion/scripts/kids-hours.py` carries them as data, and everything below reads them from there:

```python
CHILDREN = {          # local time, America/Chicago; AdGuard persistent-client names
    "ava":  {"school": ("05:30", "23:30"), "weekend": ("05:30", "23:30"),
              "devices": ["ava-phone"]},                     # + ava-ipad-mini once pinned
    "finn": {"school": ("06:00", "21:30"), "weekend": ("06:00", "22:15"),
              "devices": ["finn-pc", "finn-chromebook", "finn-roku", "finn-ipad", "finn-phone"]},
}
```

AdGuard's client tags are a fixed list (`user_child`, `device_phone`, …) with no custom values,
so the child is carried in the client *name* (`ava-…`, `finn-…`) and the script groups by that
prefix. `kids-hours.py --times` prints both windows for copying into layers 1 and 3.

Layer 1 and layer 3 are set by hand to the same times; the script prints them so they can be
copied rather than remembered.

### Steps

**1. Pin down who is who [you + agent].** MAC-based rules need stable MACs.

- On each Apple device: Settings → Wi-Fi → the network → *Private Wi-Fi Address* → **Fixed**
  (not *Rotating*, not *Off*). Fixed keeps one private MAC for this network forever, which is
  all the rules need. Rotating would defeat every layer below within a day.
- Then, with each device on WiFi, the agent reads AdGuard's lease table and names them:
  AdGuard → Settings → Client settings → add a **persistent client** per device, identified by
  **MAC** (AdGuard is the DHCP server, so MAC works), named as in `KIDS` above, tag
  `user_child`. Also add a **DHCP static lease** for each so the IP never moves, which keeps
  the query log readable.
- The agent can do the client and lease entries through AdGuard's API once bastion's `.env`
  holds `ADGUARD_USER` / `ADGUARD_PASSWORD` (the same admin login as the UI; the file is 600
  and never leaves the host). **[you]** to put them there.

> **Step 1 progress, 2026-09-27 17:30:** five devices done through the API — static lease and
> persistent client (tag `user_child`, safe search on for every engine, YouTube restricted mode
> **off** by decision, and AdGuard's **parental control** (adult-content filter) on — per client,
> so the adults' devices are untouched):
>
> | Client | MAC | Static IP | Was |
> |---|---|---|---|
> | finn-pc *(was kid-pc)* | `c8:60:00:2b:90:6f` | .150 | .124 mainpcworkstation |
> | finn-chromebook | `bc:d2:2c:87:d4:b5` | .151 | .126 |
> | finn-roku | `d4:ab:cd:57:dd:cc` | .152 | .119 playroom |
> | finn-ipad | `1c:0d:7d:17:08:fa` | .153 | .121 robbys-ipad |
> | finn-phone | `f6:99:f3:7b:b5:f7` | .154 | .106 iphone (Private Address already Fixed) |
> | ava-phone | `32:b2:62:ef:e8:29` | .155 | .125 (Private Address Fixed) |
>
> Each device moves to its static address at its next DHCP renewal (or reconnect) without
> dropping. Renamed from `kid-*` to carry the child on 2026-09-28. Still to identify: the iPad
> mini (Ava's), candidates `.120` and `.122`; it gets `.156` as `ava-ipad-mini`.

**2. AdGuard, always-on part [agent].** For every `user_child` client: SafeSearch on, and a
blocked-services list that applies at all hours — the agent proposes one from what the
query log already shows (TikTok is the obvious entry; Roblox and YouTube are yours to decide).
This is independent of the hours.

> **Built 2026-09-28 and live the same evening**: one rule per child between the markers,
> `||*^$client='finn-pc'|…,important` — AdGuard's "every domain" form, since a bare `*` is not
> a rule — on bastion's crontab every 15 minutes plus each boundary minute. `--open`/`--close`
> take `--child ava|finn`. Metrics `homelab_kids_window_open{child}` and
> `homelab_kids_rule_present{child}`.

**3. AdGuard, the hours [agent].** `kids-hours.py` on bastion's crontab at every boundary
plus every 15 minutes as a self-heal. At each run it computes whether each child's window is
open *now* and makes AdGuard's custom rules match:

```
! kids-hours: begin (managed by bastion/scripts/kids-hours.py -- do not edit by hand)
||*^$client='ava-phone',important
||*^$client='finn-pc'|'finn-chromebook'|'finn-roku'|'finn-ipad'|'finn-phone',important
! kids-hours: end
```

present when closed, absent when open, written through `POST /control/filtering/set_rules`
with every rule outside its marker block preserved. The query log then shows each off-hours
attempt as *Blocked by custom filtering rule* against the child's device name. Logs to
`~/logs/kids-hours.log`; the health check's `power_jobs`-style reader can pick up a failed run.

**3a. One device off indefinitely [agent].** `ALWAYS_OFF` in `kids-hours.py` names single
devices that stay blocked at all hours for as long as they are in the list — where an "off
until further notice" ask goes, since the overrides all expire at the next morning. Its rules
sit at the top of the same marker block, one comment carrying the reason and one rule per
device, so the query log and the file both say why:

```
! always-off: finn-pc -- playroom PC, off at Robby's request 2026-10-12
||*^$client='finn-pc',important
```

It cuts across the windows: the device is blocked inside its child's window too, and `--open`
for that child still leaves it dark. Restoring it is deleting the entry and deploying — there
is deliberately no runtime verb, so it cannot lapse quietly or be lifted from the gateway
agent's fixed verb list. `homelab_kids_device_always_off{device}` tracks it, and `--status`
prints a line per entry. **Currently in the list:** `finn-pc` (the playroom PC), since
2026-10-12.

**4. Gateway profile [you].** In the AT&T Smart Home Manager app: *Profiles & devices* → new
profile *Kids* → add the same devices → *Time limits* → the same window. This is layer 1 and
the one that actually stops a determined child. Keep *Pause internet* on that profile as the
manual override for a school night that needs it.

**5. Devices [you].** Screen Time downtime on the iPads and phones with a passcode the
children do not know; Family Link bedtime on the Chromebook; the Roku's parental PIN. Same
window.

**6. Verify [you + agent], on the first evening:**

| At | Check | Pass |
|---|---|---|
| 21:25 | one of Finn's devices loads a web page | works |
| 21:35 | same device, same page | fails; AdGuard query log shows the block against its name; Ava's phone still works |
| 21:35 | same device with DNS set by hand to `8.8.8.8` | still fails — that is layer 1 working; if it loads, layer 1 is not enforcing and the EAP fallback is next |
| 22:05 | Ava's phone | fails too |
| 06:05 | Finn's device | works again without anyone touching anything (Ava's from 05:30) |
| next morning | the *Network* dashboard | blocked queries per child overnight; `homelab_kids_rule_present` back to 0 |

**7. Rollback.** `kids-hours.py --open` removes the rule block and exits; delete the AT&T
profile; the persistent clients and static leases can stay (they are just names).

**8. From Slack [agent].** Since 2026-09-28 the OpenClaw agent can do the one-shots: the
`kids-internet` skill (`.claude/skills/kids-internet/`, deployed to forge) runs
`scripts/kids-internet.py status|open|close [ava|finn]`, which is one SSH call to bastion
with a fixed verb (`kids-status`, `kids-open-ava`, …) granted on the agent's key line and
handled by `shared/openclaw-cmd`. Same shape as `plex-power`. The agent cannot change the hours.
Since 2026-09-30 an override is a stored exception with an expiry (`~/logs/kids-hours-overrides.json`
on bastion): `--open`/`--close` last until that child's next morning start, `--until HH:MM` sets
a shorter one, `--clear` ends it. First use: Ava's project night, 2026-09-30.

### What this deliberately does not do

- No packet-level enforcement on the homelab itself. bastion is not a router and making it
  one is a re-architecture the DHCP runbook already declined.
- No content filtering beyond SafeSearch, AdGuard's parental-control filter (adult content,
  turned on per child client on 2026-09-27) and the services list. Anything finer is a
  different conversation.
- No attempt to catch DNS-over-HTTPS. Layer 1 makes it unnecessary; layer 2 would need
  AdGuard's DoH-blocking lists and still miss some.

### Reporting

Superseded by the observability plan: `kids-hours.py` will publish `homelab_kids_window_open`
and a per-client blocked count through the textfile collector, the *Network* dashboard shows
them, and a failed run becomes a Grafana alert rule in that plan's phase 3. No Slack report.

---

## Part 2 — logging the sauna, the thermostat and the sprinklers

### What each device actually offers

| Device | Reachable how | Auth | What we can read | Status |
|---|---|---|---|---|
| **HUUM sauna (UKU WiFi)** | cloud only, `https://api.huum.eu/action/home/status` (the `sauna.huum.eu` host in the library docs answers empty) | HUUM app username + password, HTTP basic | status code (offline / heating / not heating / locked / emergency stop), temperature, target, door open, humidity and steamer if fitted, light, session start/end | **usable now** |
| **Rachio** | cloud only, `https://api.rach.io/1/public/…` | API key from the app (Profile → API key → Copy), `Authorization: Bearer` | device online, rain delay, current schedule, zones, and a per-day event list (schedules and zones started, stopped, completed, weather skips); webhooks exist but need an inbound URL | **usable now**; 3 500 requests a day, reset at midnight UTC |
| **ecobee** | cloud API | needs a developer API key — **ecobee is not accepting new developer registrations** | (temperature, humidity, HVAC mode, equipment running, remote sensors, if it were reachable) | **blocked for a script of our own** |

For ecobee there are two real routes, both bigger than a poller: pair it over **HomeKit** from
bastion (local, no cloud, needs a HomeKit client library and the code on the thermostat), or run
**Home Assistant**, whose ecobee integration signs in with the consumer username and password
since 2026.3 and would also cover HUUM and Rachio with history and graphs. Both are a project.
**Recommendation: build the two pollers now, leave ecobee out, and decide on Home Assistant
only if the thermostat data turns out to matter** — at which point it replaces the pollers
rather than joining them.

### Design

One script, `bastion/scripts/iot-poll.py`, on bastion (always on, already holds the Slack
token), one module per device, credentials in bastion's `.env`
(`HUUM_USERNAME`, `HUUM_PASSWORD`, `RACHIO_API_KEY`), never in git.

- **Every 5 minutes:** HUUM status; Rachio device and current schedule. Each poll appends one
  JSON line to `~/logs/iot/<device>-YYYY-MM.jsonl` — small, greppable, and inside the
  `Documents`-style paths the Sunday sync already carries to vault once `~/logs` is added to it.
  While the sauna reports *heating*, the HUUM poll tightens to every minute so a session's
  temperature curve is captured.
- **Once a day at 06:55**, ahead of bastion's 07:00 health report: `iot-poll.py --report`
  reads yesterday's lines and posts one `kind: "iot"` report to `#homelab-logs` in the same
  shape as everything else:

  ```json
  {"host": "bastion", "kind": "iot", "status": "ok", "ts": "…",
   "findings": [],
   "metrics": {
     "sauna_sessions": 1, "sauna_minutes": 64, "sauna_peak_c": 84,
     "sprinkler_runs": 2, "sprinkler_minutes": 38, "sprinkler_skips": 1,
     "kids_window_closed_now": false, "kids_blocked_queries": 212 } }
  ```

  Findings are reserved for things a person should know: the sauna in emergency stop, steamer
  without water, the sauna door open while heating for more than a few minutes, Rachio offline
  for a day, a rain-delay that has been on for a week, or a poll that has failed for six hours.
  Everything else is a metric, and the digester prints metrics without escalating.
- **No control.** The script reads. Turning the sauna on from Slack is a nice idea and a
  separate decision, because it means the agent holding credentials that can heat a room.

> **Built 2026-09-27 as part of the observability plan's phase 2**, with one change: the data
> goes to Prometheus gauges and the Postgres events table on bastion and is read in Grafana's
> *Sauna and Sprinklers* dashboard, rather than to JSON files and a Slack report. Findings (an
> emergency stop, a dry steamer, Rachio offline for a day, a poll failing for hours) become
> Grafana alert rules in that plan's phase 3. HUUM's real host is `api.huum.eu`; the password had
> to be reset before it was accepted. Rachio: one Gen-1 8-zone controller, two zones enabled.
>
> **2026-10-07, the first thing the data was good for.** A session armed on the controller
> for a delayed start vanished on 2026-10-06 four minutes after the controller rebuilt its
> cloud connection (visible as a DNS lookup of its AWS IoT broker in AdGuard's log in Loki).
> The controller had held one connection for six days until the 2026-10-04 power outage and
> has reconnected many times a day since. Built in response: `huum_armed`, a
> `sauna/schedule-lost` event, two dashboard panels (reconnects per day, lost schedules),
> and the first two provisioned Grafana alert rules with a Slack contact point to
> `#homelab-status` — the start of the observability plan's phase 3.
>
> **2026-10-09, stop-gap:** `bastion/scripts/sauna.py` schedules a start on bastion and sends
> the plain start command at the minute (also from Slack through the `sauna` skill). It is a
> stop-gap by Robby's decision: the pending start does not appear on the controller or in the
> app, and seeing it there is preferred. The real fix is the radio by the sauna in
> [plan-wifi.md](plan-wifi.md), scheduled for 2026-10-10; the script stays as the backup.

### Steps

1. **[you]** Put `HUUM_USERNAME`, `HUUM_PASSWORD` and `RACHIO_API_KEY` in
   `/home/robby/homelab-deployed/bastion/.env` (mode 600; deploy never touches it). The HUUM
   ones are the app login; the Rachio key is in the app under Profile.
2. **[agent]** `iot-poll.py` with `--dry-run` printing what it would log; crontab entries;
   deploy. *Done 2026-09-27; there is no `--report`, Grafana reads the data instead.*
3. **[agent]** Add `~/logs/` to what `backup-to-tower.sh` stages, so the IoT history and the
   other logs reach vault weekly. *Done 2026-09-27 (`~/logs/` and `~/backups/` are mirrored).*
4. **[you]** Take a sauna. **[agent]** Confirm the session appears in the next morning's digest
   with minutes and peak temperature.
5. **[agent]** After a week, look at what the data is actually good for and prune the metrics
   that nobody reads.

### Identifying the devices on the LAN

Not needed for logging (all three are read through their clouds), but worth doing once so the
network map is complete and so an offline device can be told apart from a cloud outage. Today's
query log shows no device talking to HUUM, ecobee or Rachio domains, which means all three were
still on their old router leases with the router as DNS; they re-lease within 24 hours of the
router's DHCP going off, and the agent will name them in AdGuard when they appear. One
candidate is already visible: an Espressif-chipset device at `.118` talking to AWS IoT, which
is the shape of either the UKU controller or the Rachio.

---

## Order of play

1. **This week:** Part 2 steps 1–2 (credentials, then the poller) — small, self-contained, no
   effect on anyone. Part 1 step 1 (fixed private addresses, persistent clients) at the same
   time, because it is needed by everything after and costs nothing.
2. **One evening with the children home:** Part 1 steps 2–6 in one sitting, verify at 20:05.
3. **After a week of both:** prune the IoT metrics, and revisit whether ecobee earns Home
   Assistant.

## Sources

- TP-Link, [Configuring Standalone EAP — Scheduler](https://www.tp-link.com/us/configuration-guides/configuring_eap_standalone_eap/?configurationId=18698)
- AT&T, [Set Content Controls with Smart Home Manager](https://www.att.com/support/article/u-verse-high-speed-internet/KM1336815/) and [pausing internet by profile](https://www.att.com/device-support/article/129534/att/bgw320/)
- AdGuard Home, [`$client` modifier](https://github.com/AdguardTeam/AdGuardHome/wiki/Hosts-Blocklists), [persistent clients](https://github.com/AdguardTeam/AdGuardHome/wiki/Clients), and the OpenAPI spec (`/filtering/set_rules`, `/clients/add`, `blocked_services_schedule`)
- HUUM: [pyhuum](https://github.com/frwickst/pyhuum) (`sauna.huum.eu/action/home/status`, basic auth, status codes 230–233/400) and the [Home Assistant integration](https://www.home-assistant.io/integrations/huum/)
- Rachio: [authentication](https://rachio.readme.io/reference/authentication.md), [rate limiting](https://rachio.readme.io/reference/rate-limiting.md), [webhook types](https://rachio.readme.io/reference/sample-webhook-json.md)
- ecobee: [developer portal notice](https://www.ecobee.com/en-us/developers/) ("not currently accepting new developer registrations"); [Home Assistant integration](https://www.home-assistant.io/integrations/ecobee/) (username/password sign-in since 2026.3)
