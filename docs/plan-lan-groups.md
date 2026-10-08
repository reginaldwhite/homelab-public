# Plan: every device in a group, and what "sandboxing" can mean on this network

Written 2026-09-28 after the children's windows went per-child. The next question was: put
*every* device on the LAN into a named group by address, and see what isolation the hardware
actually allows. This is the proposal; the inventory below is what AdGuard, the neighbour
table and the `lan_clients` table showed this morning, with vendor guesses from the MAC prefix
that need confirming. **[you]** items need a phone, an app or a password; **[agent]** items run
from the laptop through AdGuard's API.

## What the hardware can and cannot do

- **The BGW320 does no VLANs and no LAN-to-LAN filtering.** Every device on the wire sees every
  other. There is no setting on it that isolates one device from another.
- **The EAP772 (standalone) can isolate WiFi clients per SSID.** Up to eight SSIDs; each can be
  a *Guest Network*, which blocks that SSID's clients from the LAN subnet and from each other
  while allowing the internet. That is the only real sandbox available without new hardware,
  and it applies only to WiFi.
- **AdGuard sees every DNS lookup and can treat clients differently** — per-client blocklists,
  blocked services, safe search, and the children's rules — but a device that brings its own
  DNS bypasses it. It is control of *names*, not packets.
- **Real segmentation** (a VLAN per group with firewall rules between them) needs a router that
  does VLANs behind the BGW320 in IP-passthrough mode, plus the EAP772's tagged SSIDs. That is
  a new device and an afternoon; it is *Option C* below, not this plan's default.

## Inventory, 2026-09-28 morning

Vendor from the MAC prefix where the prefix is well known; `?` where it is not. `Private` means
an Apple randomised address (starts with 2, 6, A or E in the second digit), which is fine as
long as the device is set to *Fixed*.

| IP | MAC | Hostname / name | Vendor guess | Proposed group | Confirm |
|---|---|---|---|---|---|
| .1 | 48:e2:ad | router | AT&T BGW320 | infra | |
| .2 | a8:29:48 | eap772 | TP-Link EAP772 | infra (static already) | |
| .10 / .12 / .13 / .212 | — | bastion, forge, cinema, vault | | infra | |
| .20 | 8c:a9:6f | — | ? | infra? | **what is it** (static, never leases) |
| .228 | 2c:fe:e2 | — | ? | ? | **what is it** (static, never leases) |
| .104 | fe:39:48 | iphone | Private | adults (Robby's phone?) | confirm |
| .105 | 5a:c8:92 | macbookpro | Private | adults | |
| .121 | 0e:cb:3b | mac | Private | adults | whose Mac |
| .123 | 68:ca:c4 | marys-iphone | Apple | adults | |
| .119 | 34:f6:8d | robbys-ipad-2 | Apple | adults | |
| .120 | 0c:2a:69 | — | Apple? | **Ava's iPad mini?** | check Settings → Wi-Fi on the mini |
| .122 | d4:54:8b | — | ? | the other candidate | |
| .126 | 34:d2:70 | — | ? | ? | **what is it** (took the Chromebook's old address) |
| .124 | c4:f7:c1 | living-room | Apple TV (also the HomeKit/Thread hub: it advertises as an IPv6 router) | media | |
| .127 | 50:32:37 | office | Apple TV | media | |
| .112 | 20:f8:5e | masterbedroomfa | ? "fan"? | iot-home | **what is it** |
| .152 | d4:ab:cd | finn-roku | Roku | kids-finn (done) | |
| .114 / .115 / .117 / .131 | 88:29:bf / 4c:60:ad / e8:9f:6d / 58:a8:e8 | amazon-… | Amazon Echo ×4 | iot-amazon | which room is which |
| .116 | d4:91:0f | amazonplug09as | Amazon smart plug | iot-plugs | |
| .109 / .111 | 20:23:51 | hs200, — | TP-Link Kasa switches | iot-plugs | |
| .110 / .113 / .132 | 60:83:e7 | —, p105, — | TP-Link Tapo plugs | iot-plugs | |
| .130 | 3c:78:95 | p115 | TP-Link Tapo plug | iot-plugs | |
| .128 | d4:8a:fc | huum | HUUM sauna controller | iot-home | |
| .134 | 44:61:32 | my-ecobee | ecobee | iot-home | |
| .118 / .133 | ac:0b:fb | — | Espressif (ESP32-based) ×2 | iot-home | **what are they** (Rachio is not yet seen anywhere) |
| .125 | a4:d7:3c | epson | Epson printer | iot-home | |
| .150–.155 | — | finn-*, ava-phone | | kids (done) | |

Old leases still in the tables and to be ignored: `.108` (the Epson's previous address),
`.166` (an Echo's previous address), `.106` and `.124`/`.126`/`.119`/`.121`/`.125` as the
children's devices' previous addresses.

## Groups and addresses

Static leases in AdGuard by MAC, one block of addresses per group, so the address alone says
what a device is and the dashboards can colour by range. The dynamic pool shrinks to the bottom
of the range, so **anything with an address in the dynamic pool is, by definition, a device
nobody has named yet** — which is the alert that matters most for security here.

| Group | Addresses | Members | AdGuard treatment |
|---|---|---|---|
| infra | .1–.29 | router, AP, the four hosts, the two unknown statics once identified | none |
| dynamic / unknown | .103–.129 | whatever just joined | default settings; a Grafana rule fires when a lease appears here and stays 24 h |
| adults | .130–.139 | phones, laptops, Robby's iPad | default settings |
| media | .140–.149 | the two Apple TVs, (the Roku stays with Finn) | default |
| kids | .150–.159 | as today, plus `ava-ipad-mini` at .156 | done |
| iot-amazon | .160–.169 | the four Echos, the Amazon plug | blocked services: none needed; a telemetry blocklist enabled for the group |
| iot-plugs | .170–.179 | Kasa and Tapo | same |
| iot-home | .180–.199 | HUUM, ecobee, the two Espressif devices, the printer | same |

Each device becomes a persistent client in AdGuard named `<group>-<thing>` (`amazon-kitchen`,
`plug-sauna-heater`, `appletv-office`) with a `device_*` tag, exactly as the children's were
done: the `lan_clients` table and the *Network* dashboard then show the group in the name.

## Sandboxing options, honestly

**A. Names only (this plan's default).** Every device grouped and named; the "unknown device"
alert; per-group DNS blocklists for the IoT groups (the AdGuard *Blocklists* page has curated
IoT-telemetry lists). Costs nothing, isolates nothing: a compromised plug can still reach the
vault host's SSH port — where it meets a key-only login and ufw, which is the real defence and
already exists.

**B. A second SSID for IoT, with client isolation on.** *(2026-10-07: now planned in detail in [plan-wifi.md](plan-wifi.md), on a UniFi AC Lite placed by the sauna rather than on the EAP772.)* On the EAP772: SSID `home-iot`, *Guest
Network* ticked, own password. Move the plugs, HUUM, the two Espressif devices and the printer
to it; they only ever talk to their clouds and would not notice. **Do not move** the Echos if
multi-room audio or Echo-to-Echo calling is used (they need each other on the LAN), nor the
ecobee if it is paired with HomeKit (the Apple TV hub must reach it), nor the Apple TVs. Real
isolation for the devices that get it, WiFi only, about an evening including re-pairing each
plug in its app. This is the recommendation if the point is "a cheap plug cannot see bastion".

**C. VLANs.** A small VLAN-capable router (the TP-Link ER605 or ER7206 fits the EAP772's Omada
family) behind the BGW320 in IP passthrough, the EAP772 broadcasting tagged SSIDs per group,
firewall rules between VLANs, and the wired PC on an access port. The only way to isolate the
wired devices and to give the Echos a segment that still lets them see each other. New hardware,
an afternoon, and it changes the DHCP/DNS plumbing again (bastion serves each VLAN, or the
router does). Worth it only if B turns out not to be enough.

## Steps

1. **[you]** Fill the *Confirm* column: the two static unknowns (.20, .228), .126, .112, the
   two Espressif devices, which Echo is in which room, whose Mac .121 is, and the iPad mini's
   address (Settings → Wi-Fi → the network → IP Address).
2. **[agent]** Static leases and persistent clients for every row through the API, dynamic pool
   shrunk to .103–.129, `ava-ipad-mini` added to `kids-hours.py`. Devices move on their next
   renewal; nothing drops.
3. **[agent]** The *Network* dashboard coloured by group; an alert rule (observability phase 3)
   for a lease in the dynamic pool older than a day.
4. **[you + agent]** Decide between A and B. If B: create the SSID on the EAP772, move the
   plugs first and watch them for a day before moving the rest.
5. **[agent]** IoT telemetry blocklist enabled for the three IoT groups; a week later, read the
   query log and prune anything it broke.

## Not doing

- Isolating the wired PC or the Echos without option C.
- Replacing the BGW320. It stays the gateway; everything above lives behind it.
- MAC-based rules on the gateway. AT&T's app can pause a device but cannot group them.
