# LAN client register

The one list of what is on the network: every device by MAC, what it is, the address it is
pinned to (or will be), and — for wireless devices — the SSID and access point it belongs on.
Kept current by hand as devices are identified; the live view is the *Network* dashboard,
fed by AdGuard (names, leases) and the Omada controller (AP, SSID, band, signal). When this
file and the dashboard disagree, one of them needs editing.

Address ranges come from [plan-lan-groups.md](plan-lan-groups.md); SSID and AP targets from
the revision of 2026-10-10 in [plan-wifi.md](plan-wifi.md):

| SSID | Bands | Security | Guest (isolation) | Schedule | APs | For |
|---|---|---|---|---|---|---|
| `WhiteHouse` | 2.4 + 5 + 6 | WPA2/WPA3; password rotated after the moves, kids excluded | off | none | both | adults' phones, laptops, iPads |
| `WhiteHouse_IoT_Lan` | 2.4 | WPA2 | off | none | both | every Echo (the master-bedroom Echo, the office-bath, Finn's and Ava's Spots — never on a scheduled SSID), ecobee, Haiku fan, Rachio (Robby's call 2026-10-23) |
| `WhiteHouse_IoT` | 2.4 | WPA2 | **on** | none | **both** for now (the HUUM would not join `_Office` on 2026-10-23 and sits here on the closet AP; pin to Default once it does) | upstairs cloud-only devices: the backyard, carport and bedroom light switches, the Amazon plug; the HUUM until it moves |
| `WhiteHouse_IoT_Office` | 2.4 | WPA2 | **on** | none | **closet AP only** (AP group Office) | basement cloud-only devices: the three Tapo P105s, the office light switch, the vault plug (-28 on the closet AP, so the vault is beside it), and the HUUM once it accepts the name. Added 2026-10-22 so a closet radio restart cannot send them upstairs |
| `WhiteHouse_Ava` | 2.4 + 5 | WPA2/WPA3 | off | off 23:30 every night, on 05:30 (since 2026-10-25; was 22:00 / 23:00) | both | Ava's phone, Chromebook, iPad mini |
| `WhiteHouse_Finn` | 2.4 + 5 | WPA2/WPA3 | off | off 21:30 Sun–Thu / 22:15 Fri–Sat, on 06:00 | both | Finn's phone, iPad, Chromebook, Roku (the PC is wired: DNS rule only) |
| `WhiteHouse_Guest` | 2.4 + 5 | WPA2, simple password, not given to the kids | **on** | off 02:00, on 08:00 | both | visitors; MAC group `kids-devices` denied (catches the Roku and Chromebooks; phones and iPads randomise their MAC on a new SSID, so the password is the real control) |

Decided with Robby 2026-10-11 (replaces the 2026-10-10 table: one isolated IoT SSID, a
LAN-visible one, one SSID per child, a scheduled guest) and **live in the controller the same
day** — schedules, the kids' MAC group and the guest deny list were applied with
`bastion/scripts/omada-ssid.py` (`list` shows the current state). The DNS night-time rules stay
as the per-child layer underneath; they also carry the overrides. Devices still have to be
joined to their SSIDs by hand, one app at a time.

Until the second EAP772 is in, `WhiteHouse_IoT` lives on the UniFi AC Lite and only the
HUUM is on it; everything else is on `WhiteHouse` on the one EAP772.

Columns: *Fixed IP* is the static lease to set (✅ when done); *Signal* is the last reading on
the upstairs EAP772 from the 2026-10-10 map, which decided the AP column.

## Infrastructure

| MAC | IP | Device | Notes |
|---|---|---|---|
| `48:e2:ad:0b:25:0a` | .1 | AT&T BGW320 gateway | DHCP off; WiFi off since 2026-10-10 except `WhiteHouse_Office` 5 GHz for the two laptops until the second EAP772 arrives |
| `a8:29:48:16:dd:07` | .2 ✅ | TP-Link EAP772, upstairs ceiling | Omada-managed; 2.4 GHz ch 1, 5 GHz ch 40, 6 GHz ch 69 |
| `a8:29:48:81:29:0b` | .4 ✅ | TP-Link **EAP770**, utility closet (`eap770-closet`) — arrived and adopted 2026-10-20; the box shipped a 770, not the 772 the plan names, and Omada manages both the same way | Omada-managed; 6 GHz on from the start (plan-wifi step 3) |
| `80:2a:a8:78:18:6a` | .3 (free since 2026-10-20) | Ubiquiti AC Lite, by the sauna (`ac-lite`) — **retired 2026-10-20** with the UniFi stack; the closet EAP770 carries that cell | spare, with its 24 W injector; the AC Pro may return for outdoor coverage (indoor-rated, so under cover) |
| — | .10 | bastion | wired |
| — | .12 | forge | wired |
| — | .13 | cinema | wired |
| `e0:cb:4e:9d:02:6a` | .212 ✅ | vault (Unraid) | wired |
| `8c:a9:6f:17:45:75` | .20 ✅ | **Denon AV receiver, living-room theatre** (`living-room-denon`), wired | Address and DNS set by hand on the receiver, so it bypasses AdGuard (never asks it anything). Switch it to DHCP in its network menu: the static lease keeps it at .20 and its DNS becomes AdGuard's |

## People's devices — `WhiteHouse` (adults), `WhiteHouse_Ava` / `WhiteHouse_Finn` (scheduled)

| MAC | IP | Fixed IP | Device | Owner |
|---|---|---|---|---|
| `fe:39:48:a3:13:8f` on `WhiteHouse`, `ee:96:db:cc:e4:46` on `WhiteHouse_Office` | .104 / .110 | .130 ✅ (the `WhiteHouse` address) | **Robby's iPhone** (`robby-iphone`) — one private address per SSID; both map to the one AdGuard name | Robby — the office-SSID address goes away with the gateway WiFi when the second EAP772 is in |
| `5a:c8:92:98:1f:e6` on `WhiteHouse`, `72:b6:f7:79:f4:5a` on `WhiteHouse_Office` | .105 / .137 | .131 ✅ (the `WhiteHouse` address) | **MacBook Pro, personal** (`robby-macbook`; one private address per SSID, both under the one name) | Robby — the office-SSID address goes away with the gateway WiFi |

| `0e:cb:3b:51:53:d2` on `WhiteHouse`, `9a:e1:bb:5b:12:e7` on `WhiteHouse_Office` | .121 / .139 | .132 ✅ (the `WhiteHouse` address) | **MacBook, work** (`robby-work-macbook`; one private address per SSID, both under the one name) | Robby — the office-SSID address goes away with the gateway WiFi |
| `68:ca:c4:c7:3a:ca` | .123 | .133 | Sam's iPhone | Sam |
| `34:f6:8d:3d:86:ab` on `WhiteHouse`, `86:a0:f8:02:8a:16` on `WhiteHouse_Office` | .119 / .138 | .134 ✅ | **Robby's iPad** (`robby-ipad`; one private address per SSID, both under the one name) | Robby |
| `c8:60:00:2b:90:6f` | .150 ✅ | — | **Playroom PC** (`finn-pc`), wired only | Finn — in his night-time DNS rule under this name, and in `ALWAYS_OFF` since 2026-10-12, so it is blocked at all hours until that entry goes |
| `bc:d2:2c:87:d4:b5` (Intel radio) and `fc:01:7c:7e:75:3a` (Foxconn) | .151 ✅ / .157 ✅ | — | **Finn's Chromebook** (`finn-chromebook`) — Robby identified the Foxconn address on 2026-10-10 (first seen 10-09); the Intel one was the original identification from 09-27. Both are under the one AdGuard name so the night-time rule covers whichever is the real one; when the Chromebook is next on, the active address tells us which to keep | Finn |
| `d4:ab:cd:57:dd:cc` | .152 ✅ | — | Finn's Roku ("Playroom" in Omada — so the Roku is the playroom device at -75 on 5 GHz, not the PC) | Finn |
| `1c:0d:7d:17:08:fa` | .153 ✅ | — | **the office iPad** (`finn-ipad`) — the iPad Finn uses; kept under his name because it is in his night-time DNS rule | Finn |
| `f6:99:f3:7b:b5:f7` | .154 ✅ | — | Finn's phone | Finn |
| `32:b2:62:ef:e8:29` | .155 ✅ | — | Ava's phone | Ava |
| `ca:03:14:6d:c8:ef` on `WhiteHouse_Ava` | .120 | — | **Ava's phone, the address it uses on her own SSID** (private MAC per SSID; seen 2026-10-24 on `WhiteHouse_Ava`, ceiling AP, -76). Added to her AdGuard client `ava-phone` on 2026-10-25, so the DNS rule covers it; no static lease yet (it holds .120 dynamically) | Ava |
| `d4:54:8b:b8:67:17` | .122 | .158 ✅ | **Ava's Chromebook** (`ava-chromebook`; Intel radio) — Robby's call on 2026-10-10: it left the network at 11:14 the day she did. In her night-time DNS rule since the same day | Ava |
| ? | ? | .156 | Ava's iPad mini | Ava — MAC still to read from the mini |

## Media — `WhiteHouse`

| MAC | IP | Fixed IP | Device | Notes |
|---|---|---|---|---|
| `c4:f7:c1:23:dc:e7` | .124 | .140 ✅ | Apple TV, living room (`living-room-appletv`) | **wired** — never seen on WiFi; HomeKit/Thread hub (advertises as IPv6 router) |

| `50:32:37:0e:81:d9` (Ethernet), `50:32:37:0e:f0:d1` (WiFi) | .113 / .127 | .141 ✅ | Apple TV, office (`office-appletv`) | **moved to Ethernet 2026-10-10**; both MACs map to the one AdGuard name, so it keeps its name if WiFi ever takes over again |
| `2c:fe:e2:98:84:4a` | .129 | .142 ✅ | **Hisense Google TV, office** (`office-hisense-tv`; "Office TV" in Omada) | Not meant to be a smart TV — the office Apple TV plays everything. **Blocked from the WiFi in Omada** (Clients → Block) 2026-10-10, confirmed gone from the client list; unblock there if it is ever wanted online |
| — | .228 | — | *dropped 2026-10-10*: nothing answers there; the Hisense MAC once seen was the office TV's wired port on a static address | — |

## Home IoT, needs the LAN — `WhiteHouse_IoT_Lan`

| MAC | IP | Fixed IP | Device | Signal | AP |
|---|---|---|---|---|---|
| `88:29:bf:d2:a5:6a` | .114 | .160 ✅ | **Echo Dot, Finn's room** (`finn-echo-spot`; shows as `amazon-aae4ce0515ce0421` in Omada until named) — basement; still on `WhiteHouse` 2026-10-24, to move to `WhiteHouse_IoT_Lan` | -74 | closet AP once moved |
| `4c:60:ad:99:cd:3e` | .115 | .161 ✅ | **Echo Dot, Ava's room** (`ava-echo-spot`; `amazon-c4cddbcc6a224fa9` in Omada until named) — basement; still on `WhiteHouse` 2026-10-24, to move to `WhiteHouse_IoT_Lan` | -74 | closet AP once moved |
| `e8:9f:6d:70:fb:f1` | .117 | .163 | **Unidentified Alexa Connect Kit appliance** — Amazon-made Espressif radio, `ack.ntp-fireos.com` time sync and little else, so something with Alexa built in (the AmazonBasics microwave was the first ACK product). It is **not** the coffee maker (that is on the plug at .176); the "Coffee Maker" name it carries in Omada since 2026-10-24 is wrong. It was re-joined to `WhiteHouse_IoT` at 20:52 on 2026-10-24 through the Alexa app under some other device name, which is the clue. Confirm by unplugging candidates while pinging `.117` | -59 | upstairs (ceiling AP) |
| `58:a8:e8:70:4e:85` | .131 | .162 ✅ | **Echo Dot, office bathroom** (`office-bath-echo-spot`; "Office Echo Dot" in Omada) — on the closet AP at -61 but still on `WhiteHouse` 2026-10-24, to move to `WhiteHouse_IoT_Lan` | -61 | closet |
| `34:d2:70:80:e7:fa` | .126 | .164 ✅ | **Echo, master bedroom** (`master-bedroom-echo`) — still on `WhiteHouse` 2026-10-24, to move to `WhiteHouse_IoT_Lan` | -72 | upstairs |
| `44:61:32:63:5f:d5` | .134 | .180 ✅ | ecobee thermostat (`ecobee`) — its room sensors talk to the thermostat by 915 MHz radio, not WiFi, so they never appear here | -50 | upstairs |
| `20:f8:5e:37:50:3f` | .112 | .181 ✅ | Haiku ceiling fan, master bedroom (`bedroom-fan`; SenseME) | -68 | upstairs |

Which Echo is in which room: Alexa app → Devices → each → *About* shows the MAC.

## Cloud-only IoT, isolated — `WhiteHouse_IoT` (ceiling AP only, since 2026-10-22) and `WhiteHouse_IoT_Office` (closet AP only)

Reconciled 2026-10-22: every device below was still on `WhiteHouse` or, for the plugs and HUUM, on an IoT SSID
broadcast by both APs. The basement set moves to `WhiteHouse_IoT_Office`; the rest to `WhiteHouse_IoT`.

| MAC | IP | Fixed IP | Device | Signal | SSID / AP |
|---|---|---|---|---|---|
| `3c:78:95:e3:05:d8` | — | .173 ✅ | **Tapo P115, vault's power plug** (`vault-plug`) — back in service 2026-10-11 with its own schedule (on Sun 01:55, off 05:00) after Wake-on-LAN proved unable to wake the board from a multi-day sleep; see docs/vault-power.md | — | `WhiteHouse_IoT`, downstairs (utility closet) |
| `20:23:51:8b:ae:24` | .109 | .174 ✅ | **Kasa HS200, backyard light** (`backyard-light-switch`; "backyard light" in Kasa) — on `WhiteHouse_IoT` since 2026-10-24 | -50 | upstairs (ceiling AP) |
| `20:23:51:24:c7:4b` | .111 | .175 ✅ | **Kasa HS200, carport driveway light** (`carport-light-switch`; "carport driveway light" in Kasa) — on `WhiteHouse_IoT` since 2026-10-24 | -59 | upstairs (ceiling AP) |
| `d4:91:0f:13:77:99` | .116 | .176 ✅ | **Amazon Smart Plug, 1st gen** (`amazon-plug`; hostname `AmazonPlug09AS`; "coffee maker" in Alexa) — **powers the kitchen coffee maker**, confirmed by Robby 2026-10-24. On `WhiteHouse_IoT` since 2026-10-24 | -59 | upstairs (ceiling AP) |
| `ac:0b:fb:7c:67:7a` | .133 | .178 ✅ | **Amazon Basics light switch, bedroom** (`bedroom-light-switch`) — the one that stayed up with the office breaker off | -67 | upstairs |
| `a4:d7:3c:40:f6:c6` | .125 | .184 ✅ | Epson printer (`epson-printer`) | wired — on no controller | — |

### `WhiteHouse_IoT_Office` — basement, closet AP only

| MAC | IP | Fixed IP | Device | Signal | SSID / AP |
|---|---|---|---|---|---|
| `60:83:e7:74:84:07` | .113 | .170 ✅ | **Tapo P105 "Office Computer"** — the monitors' plug, so they are fully off at night | -70 | downstairs |
| `60:83:e7:42:1b:99` | .110 | .171 ✅ | **Tapo P105 "Office Stereo"** — the desktop amp's plug | -73 | downstairs |
| `60:83:e7:f3:6b:4e` | .132 | .172 ✅ | **Tapo P105 "Towel Warmer"** — runs it on a 3-hour cycle instead of 24/7 | -76 | downstairs |
| `ac:0b:fb:11:2f:68` | .118 | .177 ✅ | **Amazon Basics light switch, office** (`office-light-switch`) — proven 2026-10-10 by cutting the office lighting breaker: this MAC stopped answering ping. (The stronger upstairs signal had suggested the bedroom; the AP sits directly over the office, so that inference was backwards.) | -53 | downstairs |
| `d4:8a:fc:c1:06:31` | .128 | .182 ✅ | HUUM sauna controller (`huum`) — the reconnect alert and the IoT dashboard match both .128 and .182 across the lease change | -52 on the AC Lite | downstairs (moved 2026-10-10) |
| `0c:2a:69:0f:57:89` | .120 | .183 ✅ | Rachio sprinkler controller (`rachio`; Electric Imp module) — in the office closet with the gateway and the AC Lite | -74 | downstairs — it will be a metre from the closet AP |

## How to change this file

- A device gets identified: fill in *Device*, pick its SSID/AP row, give it the next free
  *Fixed IP* in its range, then set the static lease and the AdGuard client name (the agent
  does both through AdGuard's API; say "pin .120 as rachio").
- A device moves between APs or SSIDs: change the row; the dashboard confirms within five
  minutes.
- A device leaves: delete the row and the static lease; `lan_clients` keeps its history.
