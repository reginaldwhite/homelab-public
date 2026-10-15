-- The sauna heat-up experiment (docs/plan-sauna-heatup.md): one row per heating session with
-- its setup and its responses, and the one-minute temperature samples behind them. Written by
-- scripts/iot-poll.py --poll-huum (runs and samples) and scripts/sauna.py (labels, plan state);
-- read by the "Sauna heat-up" dashboard. Idempotent, applied by apply.sh.
create table if not exists sauna_runs (
    id              bigserial primary key,
    started_at      timestamptz not null,
    ended_at        timestamptz,
    target_c        numeric,
    -- the setup, from the pending label when the session started (null = unlabelled session)
    mode            text,                   -- simple | learning
    bench_pct       int,                    -- bench vent slider, 0 closed .. 100 open
    ceiling_pct     int,                    -- ceiling vent slider
    phase           text,                   -- A1 | A2 | B | C, or null outside the experiment
    plan_seq        int,                    -- position in the phase's plan
    adaptation_run  boolean not null default false,  -- first run of a block in an adapting mode: recorded, excluded
    -- the responses, computed when the session ends (minutes from the first heating sample)
    start_c         numeric,
    t_80_min        numeric,
    t_target_min    numeric,
    overshoot_c     numeric,                -- max in the 30 min after target, minus target
    tail_slope      numeric,                -- slope over the last 10 C before target / slope over 50..70 C
    settle_sd       numeric,                -- sd of temperature 30..60 min after target, door closed
    peak_c          numeric,
    door_opened_at  timestamptz,            -- first door opening while heating
    end_state       text,                   -- idle | offline | emergency_stop
    valid           boolean,                -- usable for the fit; null until the session ends
    invalid_reason  text,
    note            text
);
create index if not exists sauna_runs_started_idx on sauna_runs (started_at desc);

create table if not exists sauna_samples (
    run_id      bigint not null references sauna_runs (id) on delete cascade,
    ts          timestamptz not null,
    temp_c      numeric,
    target_c    numeric,
    door_closed boolean,
    status_code int,
    primary key (run_id, ts)
);

-- The fitted model, rewritten by scripts/sauna-analysis.py: one row per response per term.
create table if not exists sauna_model (
    fitted_at   timestamptz not null,
    phase       text not null,
    response    text not null,              -- t_target_min | t_80_min | overshoot_c
    term        text not null,              -- intercept | bench | ceiling | bench:ceiling | start_c | cell:<b>/<c> | mode:<m>
    estimate    numeric,
    low         numeric,                    -- 95 % interval
    high        numeric,
    n           int,
    primary key (phase, response, term)
);
