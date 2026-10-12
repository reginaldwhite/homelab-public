-- Coverage survey marks (docs/plan-wifi.md, "Coverage survey"): one row each time someone taps
-- Mark on wifi.robbywhite.net. Written by bastion/wifi/survey/server.py, read back by the same
-- page and by the "WiFi survey" dashboard. Signal is as the AP hears the device; download_mbps
-- is a 25 MB fetch from bastion measured on the device. Idempotent, applied by apply.sh.
create table if not exists wifi_survey (
    id            bigserial primary key,
    ts            timestamptz not null default now(),
    spot          text not null,          -- "kitchen island": whatever was typed, trimmed
    note          text,
    device_mac    text,
    device_name   text,                   -- lan_clients name at the time, so a renamed device keeps its history
    ip            text,
    ap            text,
    ssid          text,
    band          text,                   -- 2.4 | 5 | 6
    rssi          integer,                -- dBm as the AP hears the device
    download_mbps numeric(8,1)
);
create index if not exists wifi_survey_spot_idx on wifi_survey (spot, ts desc);
