# Moving DHCP to AdGuard Home

Plan for making AdGuard the LAN's DHCP server so every client is told to use it for DNS.
**Not yet executed.** Nothing in this document has been applied.

## Why

AdGuard currently filters almost nothing. The BGW320-500 hands out **itself** as the DNS
server to every DHCP client, and it resolves publicly rather than forwarding to AdGuard:

| Query | via router `192.168.0.1` | via AdGuard `192.168.0.10` |
|---|---|---|
| `vault.robbywhite.net` | `203.0.113.10` (public IP — the LAN can't hairpin to it) | `192.168.0.10` |
| `doubleclick.net` | `142.250.9.101` (not blocked) | `0.0.0.0` |

A DHCP probe from forge (discover only, nothing applied) confirms what clients are told:

```
offered_ip = 192.168.0.233     # no reservation exists for forge's MAC
dns        = 192.168.0.1       # the router, not AdGuard
router     = 192.168.0.1
lease      = 86400             # 24h
```

This causes two distinct problems.

### 1. Bitwarden sync fails on WiFi

Clients resolve `vault.robbywhite.net` to the public IP, so LAN traffic to the vault has to
hairpin back through the gateway — and **the BGW320-500's NAT loopback cannot sustain a real
transfer**. Fetching the same page over each path from forge:

```
via 192.168.0.10 (internal):  HTTP 200, 23,139 bytes in 0.03s
via 203.0.113.10 (hairpin):   HTTP 200,  5,921 bytes, then stalls — times out at 131 B/s
                              repeated: 0 bytes / 0 bytes / 5,921 bytes, all timed out
```

It is not a path-MTU blackhole: `ping -M do -s 1472` succeeds over both paths. Small responses
get through, which is why a `/alive` check (about 20 bytes) returns 200 and looks healthy. That
made this easy to misdiagnose.

This explains the symptom exactly. A first sync — a fresh install, or a new account — transfers
the whole vault and stalls. Sync once over cellular, which never touches the loopback, and the
vault is populated; afterwards WiFi syncs are small deltas that fit under the threshold and
appear to work. Reproduced on a second phone with a new account and a clean install.

Resolving `vault.robbywhite.net` internally avoids the gateway's loopback entirely and fixes
this. (For reference, the 2026-09-22 `DecodingError` sync failures were a *separate* problem —
the server running 1.35.3 against a 2026.8.1 client — fixed by upgrading to 1.37.3.)

### 2. No ad or tracker filtering

Only hosts with DNS set statically to `192.168.0.10` (forge) are filtered. Every phone, laptop
and TV resolves through the gateway unfiltered, so AdGuard is doing almost nothing.

## Why this shape

The BGW320-500 **cannot have its DHCP server disabled**, and offers no way to change the DNS
server it advertises. Its UI (Home Network → Subnets & DHCP) only allows changing the DHCPv4
start/end addresses, with a minimum range of two. So the approach is to run a second DHCP
server and starve the router's pool down to that two-address minimum, leaving AdGuard to serve
everyone in practice.

The cleaner alternative is putting your own router behind the BGW in IP Passthrough and setting
DNS there — that's the usual recommendation, but it's hardware and a re-architecture.

## Interim fix, no project required

Problem 1 can be fixed on any individual device in under a minute, because the AdGuard rewrite
for `vault.robbywhite.net` already exists — clients just aren't asking AdGuard. On iOS:
**Settings → Wi-Fi → (i) next to the network → Configure DNS → Manual → add `192.168.0.10`**,
removing the others. The vault then resolves internally and a full sync completes over WiFi.

Worth doing on the phones that hold the vault regardless of whether the DHCP work happens,
since it also survives any future DHCP changes.

## What this costs

Decide these are acceptable before starting:

- **bastion becomes required to join the network.** It was down for hours on 2026-09-22 after a
  power event. Existing leases survive an outage for up to 24h, but a device reconnecting during
  one gets only the router's two-address pool, with unfiltered DNS.
- **The cutover is not instant.** Clients holding router leases keep renewing with the router
  until the lease expires or they reconnect. Expect up to 24h of mixed behaviour.
- **Two DHCP servers on one L2 is a race.** Starving the pool makes AdGuard win almost always,
  not always. The first couple of devices to ask after a router reboot can still land on it.
- **Devices above `.200` will renumber.** AdGuard's range is `.103–.200`; roughly ten current
  devices sit at `.204–.233` and will get new addresses. Nothing hardcodes them.

## Preconditions (verified 2026-09-22)

| Check | Result |
|---|---|
| `enp3s0` (AdGuard's configured interface) exists on bastion | ✅ `192.168.0.10/24` |
| Port 67 free on bastion | ✅ nothing listening |
| AdGuard DHCP already configured, range `.103–.200`, gateway `.1` | ✅ present but `enabled: false` |
| Statics outside AdGuard's range (`.10`, `.12`, `.13`, `.212`) | ✅ none fall in `.103–.200` |
| `NET_ADMIN` on the `adguardhome` container | ❌ **missing** — required for DHCP in Docker |
| IPv6 on the LAN (would bypass DHCPv4-supplied DNS) | ✅ none — no global address, no default route, no connectivity |

The IPv6 result matters: if the gateway were advertising IPv6 DNS via RA, clients would bypass
AdGuard over v6 no matter what DHCPv4 said. It isn't, so DHCPv4 is the whole story.

## Steps

### 1. Record the router's current pool — before changing anything

In the BGW320-500 UI (`http://192.168.0.1`, device access code on the sticker), go to
**Home Network → Subnets & DHCP** and write down the existing DHCPv4 start and end addresses.
This is the rollback value and it is not recorded anywhere else.

### 2. Grant the container NET_ADMIN

In [bastion/vaultwarden/docker-compose.yml](vaultwarden/docker-compose.yml), on `adguardhome`:

```yaml
    network_mode: "host"
    cap_add:
      - NET_ADMIN        # required to serve DHCP from a container
```

Merge to `main`, then `bastion/deploy.sh`. This has no behavioural effect on its own — DHCP is
still disabled in AdGuard's config. Confirm the capability landed:

```bash
docker inspect adguardhome --format '{{.HostConfig.CapAdd}}'   # expect [NET_ADMIN]
```

### 3. Enable DHCP in AdGuard — before shrinking the router's pool

Order matters. Enabling AdGuard first means both servers answer for a few minutes, but their
ranges are disjoint so no address conflict is possible — only inconsistent DNS. Shrinking the
router's pool first would instead leave a window where new devices can't get an address at all.

In the AdGuard UI (`https://dns.robbywhite.net` or `http://192.168.0.10:8888`) →
**Settings → DHCP settings**: interface `enp3s0`, range `192.168.0.103–192.168.0.200`,
gateway `192.168.0.1`, mask `255.255.255.0`, lease 86400. Enable it.

AdGuard advertises itself as the DNS server automatically; step 5 verifies that rather than
assuming it. Then confirm it is actually serving:

```bash
ssh robby@192.168.0.10 'ss -lunp | grep :67'          # expect AdGuardHome bound
ssh robby@192.168.0.10 'docker logs --tail 20 adguardhome'
```

If it isn't bound, stop and recheck step 2 — an enabled-but-not-serving DHCP server is the worst
state to leave the network in.

### 4. Starve the router's pool

Back in **Home Network → Subnets & DHCP**, set DHCPv4 start `192.168.0.101`, end
`192.168.0.102`. Two addresses is the minimum the UI accepts; it cannot be set to zero and the
server cannot be turned off.

### 5. Verify on a real client

Forget and rejoin the WiFi on your phone (or `dhclient -r && dhclient` on a laptop), then check:

- Phone: Settings → Wi-Fi → network → Configure DNS shows **192.168.0.10**
- `vault.robbywhite.net` resolves to `192.168.0.10`, not the public IP
- **Bitwarden completes a full first sync on WiFi** — the decisive test. Use a fresh install or
  a new account, since an already-populated vault only transfers small deltas and will appear to
  work either way
- A blocked domain returns `0.0.0.0` — e.g. `dig doubleclick.net`
- AdGuard UI → DHCP settings shows the lease, and Query Log shows the client by name

### 6. Transition the rest

Devices keep their router leases until those expire (≤24h) or they reconnect. Either wait a day
or reboot/reconnect the ones you care about. AdGuard's Query Log is the measure of progress: it
should gradually show every device on the network.

## Rollback

Fast path, in this order:

1. AdGuard UI → DHCP settings → disable. (Or `enabled: false` in
   `/home/robby/vaultwarden/agh-conf/AdGuardHome.yaml` and restart the container.)
2. Restore the router's DHCPv4 range to the values recorded in step 1.
3. Reconnect affected devices to force a fresh lease from the router.

Existing leases keep working throughout, so this is not an outage — devices just drift back to
router DNS as they renew.

## Known drift risk

AdGuard's own config lives in `/home/robby/vaultwarden/agh-conf/AdGuardHome.yaml`, **outside
this repo**, so enabling DHCP is not captured in git the way the compose change is. It is backed
up — `backup-to-tower.sh` rsyncs all of `/home/robby/vaultwarden/` to vault — but the repo will
not show that DHCP is on. Only the `NET_ADMIN` capability is visible here, which is a hint but
not the setting itself.

## Sources

- [AT&T BGW320-500 IP Passthrough / DHCP discussion](https://community.ui.com/questions/ATandT-BGW320-500-IP-Passthrough-DHCP-question/95207334-337e-4ad7-a91b-9267fbf16d90)
- [Unifi OS – AT&T BGW320 Gateway Setup](https://www.stevenz.blog/udm-pro-att-bgw320-gateway-setup/)
- [Pi-hole: help configuring DHCP alongside an ISP gateway](https://discourse.pi-hole.net/t/help-configuring-dhcp/81533)
- [Disabling AT&T gateway DHCP — reported failure modes](https://forums.tomshardware.com/threads/disable-att-fiber-modem-router-dhcp-and-use-ubiquiti-dream-machine-for-routing-for-home-network-and-enabling-port-forwarding-for-game-server.3742078/)
