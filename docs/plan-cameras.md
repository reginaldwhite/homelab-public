# Cameras, with nothing in the cloud

Written 2026-10-12. Three cameras to start: the **carport** looking down the driveway, the
**front door**, and the **living room**. Recording, detection and viewing all stay on the LAN;
no camera, recorder or app ever needs an account with anyone, and nothing leaves the house
unless Robby later adds a VPN to reach it from outside.

## What it gives

- Continuous recording for about a month and **object events** (a person at the door, a car in
  the driveway) for longer, searchable by camera and time from a phone on the LAN.
- Events in the homelab's own events table, so "every person at the front door this week" is a
  Grafana query, and a camera that stops recording is an alert like any other job.
- A path to Home Assistant later (everything is on MQTT), without deciding that now.

## Principles

1. **Nothing in the cloud, by construction, not by setting.** Cameras get a fixed address with
   **no default gateway** on the camera itself, so they cannot reach the internet no matter what
   their firmware wants; the recorder is open-source and local; viewing is through Caddy on the
   LAN like Grafana. "Cloud off" in a vendor app is a preference; no gateway is a fact.
2. **Wired, PoE, RTSP.** No WiFi cameras (a battery for exactly the AP troubles of the last
   week) and nothing that only works through its maker's app.
3. **The recorder runs where the lights are on.** bastion is always on; forge sleeps 23:00–05:00,
   which is when cameras matter most, and keeping a 7700K and a 5060 Ti awake all night for three
   cameras is the vault-24/7 argument again. bastion's i3-7100T decodes video in hardware (Quick
   Sync) and detection runs on the same iGPU through OpenVINO (a Coral accelerator if that proves slow), so the CPU barely notices.
4. **The indoor camera is a different kind of thing.** The living room watches the family, not
   the street. It records on events only, it can be switched off from the LAN, and the household
   knows it is there. See *Living room* below.

## Components

| Part | What | Why this one |
|---|---|---|
| Recorder | **Frigate** (Docker, on bastion) | Open source; local object detection; continuous + event recording; its own UI; MQTT out; the standard pairing for the cameras below. Pinned tag, `packages/frigate.md` in the upgrades skill. |
| Detector | **OpenVINO on bastion's HD 630 iGPU** first; a **Coral USB Accelerator** only if measured inference is poor | Frigate's OpenVINO detector runs on 6th-gen Intel and newer; three cameras at 5 fps detect is ~15 inferences/s against ~20–30 ms each on this iGPU. Decide from the inference time on Frigate's System page after phase 1 (over ~100 ms, or decode and detect fighting for the iGPU, means buy the Coral). If buying: list price is $60 at Coral's distributors (Mouser, DigiKey, Seeed); Amazon resellers at $100+ are a shortage markup, not the price. Detection on the 2-core CPU itself is the one option that does not fit. |
| Decode | bastion's Quick Sync (`/dev/dri`) | Already there; Frigate's `preset-vaapi` input args. Shared with OpenVINO, which is the thing to watch in phase 1. |
| Storage | **4 TB USB 3 hard disk** on bastion, ext4, mounted at `/srv/frigate` | Sized below. A disk, not the root filesystem (88 GB free is not an NVR) and not vault (asleep six nights a week). |
| Cameras | **Reolink RLC-810A** (4K, PoE) for carport and front door; **Reolink RLC-510A** (5 MP, PoE) for the living room | Fully local (RTSP/ONVIF; the cloud is optional and the app works LAN-only), on-camera person/vehicle detection, and the camera Frigate's docs carry a dedicated preset for (`preset-http-reolink`: Reolink's RTSP is quirky, its HTTP stream through go2rtc is the reliable path). The PoE doorbell from the same family if a doorbell form factor is wanted at the front door. *Budget fallback:* Amcrest IP5M-T1179EW (Dahua OEM; fine hardware, weak app and support, which is where its rating goes). *Step up:* EmpireTech (Dahua's proper US channel, English firmware) for low-light, ~$150–200 each. *Avoid:* Hikvision/Dahua grey imports (FCC covered list, firmware provenance) and Ubiquiti (needs Protect). |
| Power | **TL-SG1005P** gigabit PoE switch (4 PoE ports, 65 W) for the cameras, uplinked to the 2.5 G closet switch | ~$40; a camera draws 5–8 W. The APs keep their POE260S injectors (2.5 G). *One-box alternative:* the Omada **SG2210XMP-M2** (8 × 2.5 G PoE+, 160 W, managed by the controller) powers APs and cameras together and can switch a camera's PoE port off on a schedule — see *Living room* — at about $250. |
| Viewing | `cameras.robbywhite.net` through Caddy, LAN-only, Frigate's own login | Same `remote_ip` handle and `abort` as Grafana. The Frigate app on the phone points at that name. |
| Events | **mosquitto** (MQTT) on bastion + a small subscriber writing `events` rows | Frigate publishes every detection; the subscriber is the same shape as `iot-poll.py`'s `record_event`. Home Assistant, if it ever comes, subscribes to the same broker. |

## Sizing

Three cameras, main stream 2560×1920 at ~4 Mbps, sub stream 640×480 for detection:

- Continuous recording: 4 Mbps ≈ 1.8 GB/h per camera ≈ **130 GB/day for three**.
- Frigate defaults it is sensible to keep: continuous retained **7 days** (0.9 TB), event clips
  retained **30 days** (a few hundred GB at most), snapshots 30 days. A 4 TB disk carries that with
  room to double the continuous window later.
- Network: three main streams plus three sub streams is ~15 Mbps on the camera switch's uplink;
  nothing else notices.
- bastion: Frigate ~1 GB RAM with three cameras; decode and detection on the iGPU (or a Coral).
  Current use is ~4 GB of 16. Disk I/O is a steady ~1.5 MB/s write.

## Isolation and security

- Each camera: **static IP from the register's infrastructure block, subnet mask, no gateway,
  no DNS**; MAC pinned in AdGuard so the address never moves; a row in `docs/lan-clients.md`.
  A camera with no gateway cannot reach the internet or be reached from it; Frigate on bastion
  and a phone on the LAN reach it fine. The AdGuard query log is the audit: a camera that ever
  asks for a name has been given a gateway by mistake.
- Camera admin passwords and RTSP credentials in bastion's `.env` only (Frigate reads them
  through `{FRIGATE_RTSP_PASSWORD}`-style substitution); never in `config.yml` in git.
- Frigate's UI behind Caddy with Frigate's own authentication on; the camera web UIs are
  reachable only on the LAN and only by IP; ONVIF/RTSP are the only services the cameras need.
- No port forwarding, ever. Remote viewing is a later WireGuard-on-bastion evening, LAN access
  from outside, not exposure of anything.
- Frigate's upgrade hazard, for the skill: the config schema changes between minors and
  the recordings directory layout is versioned; back up `config.yml` and the SQLite database
  (`frigate.db`, ~MB) nightly with the other archives. Recordings are not backed up — they are
  the bulk; event clips from the last week are mirrored to vault on Sunday.

## Living room

The one that needs rules rather than hardware:

- **Events only, no continuous recording** (`record.retain.days: 0`, events 14 days).
- **Off when people are home**, decided by the household, not by the camera. Two ways, pick
  one at phase 1: (a) a `cameras.py living-room off|on|status` verb on bastion that toggles the
  camera in Frigate over MQTT (`frigate/living_room/recordings/set`, `.../detect/set`) — an
  agent-runnable script with an approvals entry, so "turn the living room camera off" works from
  Slack; or (b) PoE power off on the switch port on a schedule, which is physical and obvious
  but needs the managed switch. (a) is enough; (b) is the stronger guarantee.
- A visible indicator: the camera's own LED stays enabled on this one. Everyone in the house is
  told it exists and how to see whether it is on.

## Phases

### Phase 0 — parts [you]

The 4 TB disk, three cameras, the camera PoE switch (the Coral only if phase 1 says so), two Cat6 runs to the closet
(carport, front door) and one to the living room. The carport and front-door runs are the
work; the living room can be a short run to the nearest jack.

### Phase 1 — one camera, end to end [you + agent], one evening

1. **[you]** The disk on bastion, formatted ext4 and mounted at `/srv/frigate`
   with an `fstab` entry by UUID. **[agent]** `bastion/cameras/`: `docker-compose.yml` (Frigate,
   `/dev/dri` passed through (OpenVINO detector), `/srv/frigate` for media, `shm_size` per the docs,
   on `vaultwarden_default` for Caddy), `config.yml` with one camera, mosquitto; Caddy vhost;
   AdGuard rewrite; `apply.sh` grows a fourth project; `packages/frigate.md`.
2. **[you]** The first camera (front door) on the PoE switch: set its static address and **no
   gateway** in its web UI, RTSP on, cloud/P2P off, password into `.env`. **[agent]** Pin it,
   register it, point Frigate at it.
3. **Done when:** continuous recording is on the disk, a person at the door produces an event
   with a clip, the AdGuard query log shows the camera has never asked for a name, and
   `cameras.robbywhite.net` works on the phone.

### Phase 2 — the other two cameras [you + agent], an afternoon

Carport (driveway) and living room, same steps; the living-room rules from above applied; the
`cameras.py` verb (option a) if chosen. Detection zones drawn in Frigate's UI (the driveway's
public-road edge masked so passing cars are not events).

### Phase 3 — the homelab around it [agent], one evening

- MQTT subscriber → `events` rows (`kind=camera`, name=camera, detail: label, score, zone).
- Grafana *Cameras* dashboard: detections per camera per day, recording segments present per
  hour, disk free on `/srv/frigate`, detector inference time.
- Alerts: a camera with no recording segments for 15 minutes; the disk under 10% free;
  Frigate down (its `/api/version` as a blackbox-http probe).
- Sunday: last week's event clips to vault; nightly `config.yml` + `frigate.db` to `~/backups`.

### Later, each its own evening

WireGuard on bastion for viewing from outside; Home Assistant subscribing to the same broker;
a fourth camera (back yard) which the switch and disk already have room for.

## Risks, named

- **bastion's blast radius grows again** (DNS, DHCP, vault, observability, WiFi controllers,
  now the NVR). Accepted for the same reason as before — the alternative is a fourth always-on
  machine — but the disk is the thing to watch: Frigate must never fill the root filesystem,
  hence its own disk and the alert on it.
- **A USB disk is a USB disk.** Fine for recordings that are inherently disposable; the config
  and database are backed up elsewhere. If it fails, Frigate stops recording and the alert fires.
- **Camera firmware wants the internet.** Some models retry cloud registration forever; with no
  gateway that is harmless noise on the LAN and nothing more.
- **The indoor camera.** The technical controls above are easy; the rule that matters is that
  the household agreed to it. If that ever stops being true, the camera comes down.

## Not doing

- Any cloud NVR, any camera subscription, any doorbell that needs an account (Ring, Nest,
  Arlo, Eufy's app-only models).
- WiFi cameras.
- Frigate on forge, or GPU detection on forge: the sleeping-host problem, and the iGPU or a Coral is
  enough for ten cameras.
- Port forwarding to anything here.
