# Alexa, locally: voice control of the homelab through the Echos

Written 2026-10-14, after two days of trying the old way. The Echos already in every room
should be able to run the homelab's existing verbs by voice, with the command staying on the
LAN once Amazon's cloud has turned speech into intent — no Alexa skill, no Lambda, no port
forwarding.

## What Robby wants to say

| Phrase | Does | Status |
|---|---|---|
| "Alexa, turn off Plex" / "turn on Plex" / "is Plex on?" | cinema suspend / Wake-on-LAN / probe (`plex-power`'s verbs) | first device |
| "Alexa, schedule the sauna at 7pm" | `sauna.py schedule 19:00` on bastion (93 °C, 180 min default) | wanted 2026-10-14; see *The sauna* |
| "Alexa, start the sauna" / "stop the sauna" / "cancel the sauna" | `sauna.py start` / `stop` / `cancel` | with the above |

Not on voice, deliberately: the children's internet windows (a voice command that any child can
issue defeats itself) and anything that changes configuration.

## What was learned the hard way (2026-10-12/13)

- **fauxmo** (an emulated Belkin WeMo socket): the Echos discovered it and the app showed the
  switch flipping, but current firmware sends WeMo commands to Belkin's cloud, never to the
  LAN; the bridge only ever saw the Echo's once-a-minute `setup.xml` poll.
- **ha-bridge** (an emulated Philips Hue bridge, on its own macvlan address 192.168.0.11 so it
  could own port 80): answered SSDP discovery on the wire and worked end to end when driven by
  hand through the Hue API, but the Echos never searched for it — no SSDP, no mDNS query for
  `_hue`, no fetch of `description.xml` — while their other multicast crossed the EAP772 to the
  LAN normally. The legacy local-discovery path both tricks rely on is gone from 2026 Echos.
- Side findings worth keeping: the AT&T gateway polls SSDP every minute; a macvlan container is
  invisible to its own host, so a listener on bastion never sees `.11`'s traffic; Omada's
  per-SSID *multicast to unicast* is on by default and did not matter here.

Code for both attempts is in git history; the caged `alexa_ed25519` key (forced command
`suspend health` on cinema) still exists and is harmless.

## The design: Matter

Echos (4th generation and later, and the Shows) are **Matter controllers** over Wi-Fi: a Matter
device on the LAN is commissioned once with a QR code and then controlled locally, officially,
by design rather than by a legacy path Amazon might close. The homelab side:

- **`matterbridge`** (Node, Docker, on bastion, host network for mDNS commissioning) with a small
  plugin of our own that exposes virtual devices and runs a command on each state change —
  the same `plex.py`/`sauna`-style verbs, the same caged SSH keys, the same logs in `~/logs`.
- **Plex**: a Matter *on/off plug*. On → wake, off → suspend, state → the port-22 probe.
- **The sauna**: Matter carries no "7pm" in an on/off command, so the time has to be encoded.
  Two ways, pick at build time:
  1. **A dimmer whose level is the hour.** "Alexa, set the sauna to 19" → `schedule 19:00`;
     "turn on the sauna" → `start`; "turn off the sauna" → `stop`/`cancel`. One device, any
     time, slightly odd phrasing.
  2. **Presets through Alexa Routines.** Virtual switches "Sauna 6pm", "Sauna 7pm", … and a
     routine per phrase ("schedule the sauna at 7pm" → turn on *Sauna 7pm*). Natural phrasing,
     a fixed menu of times — and routines are the one place Alexa can **restrict a trigger to a
     recognised voice** (Robby's voice profile), which answers the next section.

## The sauna is the one that needs a gate

Matter devices, like Hue and WeMo ones, carry no per-speaker authentication: whoever can talk
to an Echo can flip them. For Plex that is harmless. A heater is not: the earlier decision
(2026-10-12) to keep the sauna off voice stands **unless** the voice path is gated. The gate
available today is an Alexa Routine with a personalised trigger (fires only for a recognised
voice profile), which is why option 2 above is the likely shape even though option 1 is neater.
Whatever is built, `sauna.py` on bastion keeps its own guards (door open refuses to start; one
pending start at a time), and nothing on voice can change the hours or the temperature ceiling.

## Phases

### Phase 1 — Plex over Matter [agent, then you], one evening

`bastion/matter/`: `matterbridge` pinned, our plugin (`homelab-verbs`) with one device, Plex;
commissioning QR shown in matterbridge's UI (LAN-only, behind Caddy like the rest); Robby adds
it in the Alexa app (Devices → + → Matter). Done when "turn off Plex" suspends cinema and the
action is in `~/logs`.

### Phase 2 — the sauna, gated [you decide the shape, agent builds]

Option 1 or 2 from above; if 2, the routines are Robby's to create in the Alexa app with the
voice restriction on. Done when "schedule the sauna at 7pm" shows up as a pending start in
`sauna.py status`, and the same phrase from another voice does nothing.

### Later

forge ("turn on the AI"), and whatever Home Assistant brings: HA can commission to the same
Matter fabric, so nothing here is thrown away when it arrives.

## Not doing

- Any Alexa custom skill, Lambda or public endpoint; any cloud relay (Nabu Casa) for this.
- The kids' internet on voice.
- Another LAN emulation of a 2015 device.
