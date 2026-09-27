-- Events store on bastion. Runs once, when the events-db volume is first created; later
-- changes are migrations added as 002-…, 003-… (the entrypoint runs new files only on an
-- empty volume, so a migration for an existing database is applied by hand with psql).
-- See docs/plan-observability.md, "The three data shapes".

-- Things with a beginning, an end and a result: backup runs, power jobs, sauna sessions,
-- sprinkler runs, the children's window closing, deploys. Written by scripts/event.sh.
create table if not exists events (
    id         bigserial primary key,
    ts         timestamptz not null default now(),   -- when it ended, or happened
    started_at timestamptz,
    host       text not null,                        -- bastion | forge | cinema | vault
    kind       text not null,                        -- backup | power | sauna | sprinkler | kids | os-update | deploy
    name       text not null,                        -- backup-to-tower, wake-vault, session, zone 2, …
    status     text not null,                        -- ok | error | skipped
    detail     jsonb,                                -- the script's own facts: minutes, peak_c, exit code
    source_id  text                                  -- upstream id when one exists (rachio:<id>), for idempotent inserts
);
create unique index if not exists events_source_id_key on events (source_id) where source_id is not null;
create index if not exists events_ts_idx on events (ts desc);
create index if not exists events_kind_ts_idx on events (kind, ts desc);

-- The network map: every device AdGuard knows about, from DHCP leases and its persistent
-- client list. Upserted by scripts/adguard-stats.py --clients; rows are never deleted, so
-- a device that left is still there with an old last_seen.
create table if not exists lan_clients (
    mac        text primary key,
    ip         text,
    hostname   text,                                 -- what the device called itself in DHCP
    name       text,                                 -- what we call it (AdGuard persistent-client name)
    tags       text[],                               -- AdGuard client tags, e.g. user_child
    source     text,                                 -- dhcp | static | client
    first_seen timestamptz not null default now(),
    last_seen  timestamptz not null default now()
);
create index if not exists lan_clients_last_seen_idx on lan_clients (last_seen desc);
