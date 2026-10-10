-- The wireless half of the network map (docs/plan-wifi.md, phase 4): which access point,
-- SSID and band a device is on and how well it hears it, from the two WiFi controllers.
-- Written by scripts/wifi-clients.py every five minutes; AdGuard's columns in the same row
-- keep coming from adguard-stats.py. Idempotent, so apply.sh can run it on every deploy.
alter table lan_clients
    add column if not exists wifi_controller text,        -- omada | unifi: who last saw it on WiFi
    add column if not exists ap              text,        -- access point name as the controller shows it
    add column if not exists ssid            text,
    add column if not exists band            text,        -- 2.4 | 5 | 6
    add column if not exists signal_dbm      integer,     -- as the AP hears the client
    add column if not exists wifi_uptime_s   integer,     -- seconds since it associated
    add column if not exists wifi_seen       timestamptz; -- last poll that found it on WiFi
