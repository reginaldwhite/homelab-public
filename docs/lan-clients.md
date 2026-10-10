# LAN client register

The one list of what is on the network: every device by MAC, what it is, the address it is
pinned to (or will be), and — for wireless devices — the SSID and access point it belongs on.
Kept current by hand as devices are identified; the live view is the *Network* dashboard,
fed by AdGuard (names, leases) and the Omada controller (AP, SSID, band, signal). When this
file and the dashboard disagree, one of them needs editing.

Address ranges come from [plan-lan-groups.md](plan-lan-groups.md); SSID and AP targets from
the revision of 2026-10-10 in [plan-wifi.md](plan-wifi.md):

| SSID | Broadcast by | Isolation | For |
|---|---|---|---|
| `WhiteHouse` | both EAP772s, roaming | none | people's devices, media boxes |
| `WhiteHouse_Home` | both EAP772s | none (needs the LAN) | Echos, ecobee, fan, anything with local control or HomeKit |
| `WhiteHouse_IoT` | downstairs (closet) EAP772 only | Guest / isolated | fixed cloud-only devices downstairs |
| `WhiteHouse_IoT_Up` | upstairs EAP772 only | Guest / isolated | fixed cloud-only devices upstairs |
| `Ava`, `Finn` | both EAP772s, scheduled | none | phase 5 of the WiFi plan |

Until the second EAP772 is in, `WhiteHouse_IoT` lives on the UniFi AC Lite and only the
HUUM is on it; everything else is on `WhiteHouse` on the one EAP772.

Columns: *Fixed IP* is the static lease to set (✅ when done); *Signal* is the last reading on
the upstairs EAP772 from the 2026-10-10 map, which decided the AP column.

## Infrastructure

| MAC | IP | Device | Notes |
|---|---|---|---|
| `48:e2:ad:0b:25:0a` | .1 | AT&T BGW320 gateway | DHCP off; WiFi off since 2026-10-10 except `WhiteHouse_Office` 5 GHz for the two laptops until the second EAP772 arrives |
| `a8:29:48:16:dd:07` | .2 ✅ | TP-Link EAP772, upstairs ceiling | Omada-managed; 2.4 GHz ch 1, 5 GHz ch 40, 6 GHz ch 69 |
| `80:2a:a8:78:18:6a` | .136 | Ubiquiti AC Lite, by the sauna | UniFi-managed; retired when the second EAP772 is in |
| — | .10 | bastion | wired |
| — | .12 | forge | wired |
| — | .13 | cinema | wired |
| `e0:cb:4e:9d:02:6a` | .212 ✅ | vault (Unraid) | wired |
| `8c:a9:6f:…` | .20 | **unknown**, static, never leases | identify |

## People's devices — `WhiteHouse`, roaming

| MAC | IP | Fixed IP | Device | Owner |
|---|---|---|---|---|
| `fe:39:48:a3:13:8f` | .104 | .130 | iPhone (private address) | Robby? confirm |
| `5a:c8:92:98:1f:e6` | .105 | .131 | MacBook Pro, personal (private address) | Robby — on `WhiteHouse_Office` at the desk for now |
| `0e:cb:3b:51:53:d2` | .121 | .132 | Mac (private address) | work laptop? confirm |
| `68:ca:c4:c7:3a:ca` | .123 | .133 | Sam's iPhone | Sam |
| `34:f6:8d:3d:86:ab` | .119 | .134 | Robby's iPad 2 | Robby |
| `c8:60:00:2b:90:6f` | .150 ✅ | — | Finn's PC | Finn (kids group, DNS hours) |
| `bc:d2:2c:87:d4:b5` | .151 ✅ | — | Finn's Chromebook | Finn |
| `d4:ab:cd:57:dd:cc` | .152 ✅ | — | Finn's Roku | Finn |
| `1c:0d:7d:17:08:fa` | .153 ✅ | — | Finn's iPad | Finn |
| `f6:99:f3:7b:b5:f7` | .154 ✅ | — | Finn's phone | Finn |
| `32:b2:62:ef:e8:29` | .155 ✅ | — | Ava's phone | Ava |
| ? | ? | .156 | Ava's iPad mini | Ava — MAC still to read from the mini |

## Media — `WhiteHouse`

| MAC | IP | Fixed IP | Device | Notes |
|---|---|---|---|---|
| `c4:f7:c1:23:dc:e7` | .124 | .140 | Apple TV, living room | HomeKit/Thread hub (advertises as IPv6 router); 5 GHz |
| `50:32:37:0e:f0:d1` | .127 | .141 | Apple TV, office | 5 GHz |
| `2c:fe:e2:98:84:4a` | .129 | .142 | Hisense TV (Google TV; Apple TV app traffic) | 5 GHz, -75 dBm upstairs — which room? |
| `2c:fe:e2:…` | .228 | — | second Hisense interface, static, never asks DNS | same TV's other port, or another TV — identify |

## Home IoT, needs the LAN — `WhiteHouse_Home`

| MAC | IP | Fixed IP | Device | Signal | AP |
|---|---|---|---|---|---|
| `88:29:bf:d2:a5:6a` | .114 | .160 ✅ | **Echo Spot, Finn's room** (`finn-echo-spot`) | — | upstairs? — not in the kids' DNS rule, by choice so far |
| `4c:60:ad:99:cd:3e` | .115 | .161 ✅ | **Echo Spot, Ava's room** (`ava-echo-spot`) | -75 (5 GHz) | by room — not in Ava's DNS rule, by choice so far |
| `e8:9f:6d:70:fb:f1` | .117 | .163 | Echo (`amazon`) — which room? | -58 | by room |
| `58:a8:e8:70:4e:85` | .131 | .162 ✅ | **Echo Spot, office bathroom** (`office-bath-echo-spot`) | -70 | downstairs |
| `34:d2:70:80:e7:fa` | .126 | .164 ✅ | **Echo, master bedroom** (`master-bedroom-echo`) | -53 | upstairs |
| `44:61:32:63:5f:d5` | .134 | .180 | ecobee thermostat — its room sensors talk to the thermostat by 915 MHz radio, not WiFi, so they never appear here | -50 | upstairs |
| `20:f8:5e:37:50:3f` | .112 | .181 | Haiku ceiling fan, master bedroom (SenseME) | -68 | upstairs |

Which Echo is in which room: Alexa app → Devices → each → *About* shows the MAC.

## Cloud-only IoT, isolated — `WhiteHouse_IoT` (downstairs) / `WhiteHouse_IoT_Up` (upstairs)

| MAC | IP | Fixed IP | Device | Signal | SSID / AP |
|---|---|---|---|---|---|
| `60:83:e7:74:84:07` | .113 | .170 ✅ | **Tapo P105 "Office Computer"** — the monitors' plug, so they are fully off at night | -70 | `WhiteHouse_IoT`, downstairs |
| `60:83:e7:42:1b:99` | .110 | .171 ✅ | **Tapo P105 "Office Stereo"** — the desktop amp's plug | -73 | `WhiteHouse_IoT`, downstairs |
| `60:83:e7:f3:6b:4e` | .132 | .172 ✅ | **Tapo P105 "Towel Warmer"** — runs it on a 3-hour cycle instead of 24/7 | -76 | `WhiteHouse_IoT`, downstairs |
| `3c:78:95:…` | .130 | .173 | Tapo P115 (`p115`) — not seen since the gateway WiFi went off; where is it? | — | by location |
| `20:23:51:8b:ae:24` | .109 | .174 | Kasa HS200 switch (`hs200`) | -46 | `WhiteHouse_IoT_Up`, upstairs |
| `20:23:51:24:c7:4b` | .111 | .175 | Kasa HS200 switch | -57 | `WhiteHouse_IoT_Up`, upstairs |
| `d4:91:0f:13:77:99` | .116 | .176 | Amazon Smart Plug (`amazonplug09as`) | -63 | by location |
| `ac:0b:fb:11:2f:68` | .118 | .177 ✅ | **Amazon Basics light switch, bedroom** (`bedroom-light-switch`) — *inferred*: the app shows no network info for either switch; the stronger upstairs signal was taken as the bedroom one. Swap the two names if a toggle test says otherwise | -53 | `WhiteHouse_IoT_Up`, upstairs |
| `ac:0b:fb:7c:67:7a` | .133 | .178 ✅ | **Amazon Basics light switch, office** (`office-light-switch`) — *inferred*, see above | -67 | `WhiteHouse_IoT`, downstairs |
| `d4:8a:fc:c1:06:31` | .128 | .182 | HUUM sauna controller | -52 on the AC Lite | `WhiteHouse_IoT`, downstairs ✅ (moved 2026-10-10) |
| `0c:2a:69:0f:57:89` | .120 | .183 | Rachio sprinkler controller (Electric Imp module) | -74 | `WhiteHouse_IoT`, downstairs if the closet is nearer — confirm where it hangs |
| `a4:d7:3c:40:f6:c6` | .125 | .184 | Epson printer | wired? not on any controller | — |

## How to change this file

- A device gets identified: fill in *Device*, pick its SSID/AP row, give it the next free
  *Fixed IP* in its range, then set the static lease and the AdGuard client name (the agent
  does both through AdGuard's API; say "pin .120 as rachio").
- A device moves between APs or SSIDs: change the row; the dashboard confirms within five
  minutes.
- A device leaves: delete the row and the static lease; `lan_clients` keeps its history.
