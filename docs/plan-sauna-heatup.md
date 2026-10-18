# Plan: sauna heat-up experiment — heating mode first, then the vents

Written 2026-10-15, revised the same day after Robby's answers; implementation starts Sunday
2026-10-18. Builds on the HUUM poller and `sauna.py` from
[plan-kids-hours-and-iot.md](plan-kids-hours-and-iot.md) (Part 2).

## The question

The sauna has one fixed intake by the heater and two adjustable exhaust vents — sliders — one
under the top bench and one in the ceiling. Today the bench vent is closed and the ceiling vent
is open about 10 %. The controller offers two heating modes, **simple** and **learning**, set on
the local panel. The temperature probe is a remote one at the bench, so the controller reads
what a person feels. The target is 196 °F, which the controller holds as **91 °C**; it stays at
91 for the whole experiment. The sauna is indoors, on a floor partly below ground, so ambient
conditions barely move — the 26 recorded cold starts sit at 23–33 °C.

Two things to settle, in this order:

1. **Which heating mode is better** — and what "learning" actually does.
2. **Which vent positions heat fastest**, in the winning mode.

And the by-product: **how long heating takes**, well enough that "sauna at 7" can mean *ready*
at 7.

## What the data already says

Prometheus has held `huum_temperature_celsius` at 5-minute resolution since 2026-09-27.
Reconstructed from the 26 heating intervals of the last 18 days, cold evening starts only, with
the vents where they are now and whichever mode is set now:

| | cold start 23–27 °C, target 93 | notes |
|---|---|---|
| minutes to 80 °C | **60–65** | every run within that band |
| minutes to target | **80–90** | 5-minute resolution, so ±5 |
| peak | **95–98** (target 93) | 3–5 °C of overshoot |
| 91/82 °C targets (10-13/14) | 60–75 to target | |

What this settles:

- **The process is very repeatable** — the same setup gives the same curve within ±5 minutes at
  the current resolution. A 10-minute difference between setups will be obvious with two runs
  each; a 5-minute one needs the one-minute sampling below and three.
- **Start temperature still matters** even indoors: a 61 °C warm start reached target in 50
  minutes against 85 from 25 °C. Every run records it and the vent model adjusts for it, but
  with a stable room it is a small correction, not the weather problem it would be outdoors.
- **5-minute polling is too coarse** for the question: reaching target is a single sample
  crossing, so the measurement error is the poll interval. One-minute sampling while heating
  cuts that to ±1.
- Evenings where the temperature stalled at 91 and never touched 93 are the door opening when
  the session started, not the heater. A run is scored on the *heat-up*, which ends at the first
  door opening or the first target crossing, whichever is first.
- **This baseline is one cell already**: current vents, current mode. The first thing Sunday's
  code does is re-measure it at one-minute resolution.

## Responses

Per run, all from one-minute samples:

| name | definition | what it answers |
|---|---|---|
| `t_80` | minutes from heating start to first sample ≥ 80 °C | "usable" time; robust to how a mode behaves near target |
| `t_target` | minutes to first sample ≥ 91 °C | the primary response |
| `overshoot_c` | max temperature in the 30 min after `t_target`, minus 91 | what a smarter mode most likely changes |
| `tail_slope` | °C/min over the last 10 °C before target ÷ the slope over 50→70 °C | the curve's knee; < 0.7 means the heater eased off |
| `settle_sd` | std dev of temperature 30–60 min after target, door closed | how steady it holds at 196 °F — comfort, and a reason to prefer a slightly slower mode |

## Phase A — heating mode

**Setup fixed:** bench vent closed, ceiling 10 % (today's positions), target 91, cold start.

**Why blocks, not alternation.** A mode named *learning* presumably adapts from session to
session. If it does, alternating simple/learning would hand it a different history every time
and measure nothing; and its first sessions after a switch are not its real behaviour. So:

| block | runs | purpose |
|---|---|---|
| A1: the mode set today | 3 | re-measure the baseline at one-minute resolution (the 26 old runs say what to expect) |
| A2: the other mode | 6 | enough to see whether runs 1→6 drift (adapting) or are flat (just a different control law) |

Nine evenings. The ordering A1 then A2 is deliberate: the current mode has months of state, if
state is a thing, and is measured as it is; the other mode is then given six runs from a clean
switch and is judged on its **last three**.

**What "learning" is, read from the data:** if A2's curves change monotonically across the six
runs — `t_target` falling, `overshoot_c` shrinking — the mode adapts, and the plan's phase B
rules for adapting modes apply. If run 1 looks like run 6, it does not adapt in any way that
matters here, and it is simply the second control law. If it adapts but has not settled by run
6, extend A2 three runs at a time until two consecutive runs agree within 2 minutes.

**Decision rule:** compare A1 (3 runs) against A2's last three on `t_target` and `t_80` (a
two-sample t-test is plenty with a room this stable; the dashboard shows the runs side by side
anyway). The faster mode wins unless it is within 5 minutes of the other *and* clearly worse on
`overshoot_c` or `settle_sd` — in which case the steadier one wins, because 5 minutes is less
than the comfort difference of a 4 °C overshoot at 196 °F. Record the choice at the top of this
file and set the panel to it.

## Phase B — vents, in the winning mode

**Factors:** bench vent and ceiling vent, each **closed (0 %) and fully open (100 %)** — the
ends of the sliders, four cells. Today's "ceiling 10 %" is the baseline cell and already has
its runs from phase A, so it is a fifth point for free, not a sixth cell to run.

| if the winning mode… | design | runs |
|---|---|---|
| does not adapt | 2 × 2, two replicates, cell order randomised by the script | 8 |
| adapts | each cell a block of **three consecutive** runs; the first is the adaptation run and is recorded but excluded; cell order randomised | 12 |

**Model:** `t_target = β0 + β1·bench + β2·ceiling + β3·bench×ceiling + β4·start_c`, least
squares, same for `t_80` and `overshoot_c`. Output in minutes with 95 % intervals: *opening the
ceiling vent costs 9 min (95 % CI 4–14)*, and a prediction per cell at a 25 °C start. The best
cell is settled when its interval does not overlap the runner-up's; otherwise two more runs of
each of the top two, and if they still tie, pick by comfort.

**Phase C, optional:** with the better bench setting fixed, the ceiling slider at 25 % and
50 % (two runs each) — because a slider's answer is rarely at its ends, and the baseline 10 %
point already hints at the shape. Same machinery; percentages are first-class in the schema
for this reason.

**A run is invalid**, and its cell goes back in the queue, when: the door opens before target;
the start temperature is 40 °C or over (a second session of the evening is never a run); the
target is not 91; the controller goes offline mid-heat-up (`statusCode 230`); or no label was
set. Invalid runs stay in the table, flagged, and are excluded from the fit — never deleted.

## What gets built

### Data capture — lands Sunday; everything else can follow while runs accumulate

- **One-minute HUUM polling.** `iot-poll.py` gains `--poll-huum` for a `* * * * *` cron line;
  the existing `*/5` line keeps Rachio (its 3 500/day budget is why they split). One small JSON
  a minute to HUUM, 1 440 a day; Home Assistant polls it faster. The 5-minute gauges and the
  `sauna/session` event are unchanged.
- **Two tables**, migration `bastion/observability/postgres/005-sauna.sql`, applied by hand with
  psql as the file's header says:
  - `sauna_runs` — `id, started_at, ended_at, target_c, mode, bench_pct, ceiling_pct, start_c,
    t_80_min, t_target_min, overshoot_c, tail_slope, settle_sd, door_opened_at, phase, plan_seq,
    adaptation_run, valid, invalid_reason, note`. One row per heating session, labelled or not —
    unlabelled sessions outside the experiment still get their numbers, which keeps the heat-up
    estimate improving after the experiment ends.
  - `sauna_samples` — `run_id, ts, temp_c, target_c, door_closed, status_code`, one row a minute
    while heating; a 3-hour session is 180 rows.
- **Run lifecycle in the poller:** heating seen → insert a run with the start temperature and
  the pending label; each minute → a sample; door opens or target crossed → freeze the heat-up
  responses; heating stops → close the run and compute the rest. State in the existing
  `iot-state.json`.
- **Labels and the plan, in `sauna.py`:**
  ```
  sauna.py next        # the next run to set up, and sets it as the pending label, e.g.
                       #   phase A2 run 4 of 6: mode LEARNING, bench 0 %, ceiling 10 %
                       #   phase B run 3 of 8: mode LEARNING, bench 100 %, ceiling 0 %
  sauna.py label mode=learning bench=0 ceiling=10   # record what was actually set, if different
  sauna.py label clear
  sauna.py runs        # the table so far, one line a run
  sauna.py phase       # where the experiment stands; after phase A, which mode won and why
  ```
  The plan — the phase list, the block structure, the seeded random cell order for phase B —
  lives in `~/logs/sauna-plan.json`, created on first `next`. Phase B's shape (8 or 12 runs)
  is written into it when phase A is decided, by `sauna.py phase decide learning|simple`.
  These verbs are read-mostly and get `exec-approvals` entries and lines in the `sauna` skill,
  so the evening flow is: ask in Slack *"what's the next sauna run"* → the agent relays the
  line → Robby sets the panel and the sliders, checks them → starts the session as usual (panel,
  app or Slack; it makes no difference). If the sliders end up somewhere else, *"sauna label
  bench 0 ceiling 25"* corrects the pending label. The agent relays and never improvises; it
  cannot touch the vents or the panel.

### Analysis

`bastion/scripts/sauna-analysis.py`, daily at 06:50 from cron and on demand: the phase-A
comparison while in phase A (both blocks, the drift check on A2, the decision rule's verdict as
a suggestion — the decision itself is Robby's, by `phase decide`), the 2 × 2 fit in phase B, the
per-cell predictions, all written to a small `sauna_model` table (one row per term per
response: estimate, low, high, n, fitted_at) and printed to stdout. It also maintains the plain
heat-up estimate for scheduling — `t_target` as a function of start temperature over *all*
valid runs in the chosen setup. The fit is an n×n normal-equations solve in pure Python; bastion
has no numpy and does not need it for five coefficients.

### Dashboard — *Sauna heat-up* (`grafana/dashboards/sauna-heatup.json`, uid `homelab-sauna-heatup`)

| panel | shows |
|---|---|
| Heat-up curves | XY chart on `sauna_samples`, x = minutes since start, one line per run, coloured by setup; the learning-mode drift and the smart knee, if they exist, are visible here before any statistic |
| Phase A: simple vs learning | `t_target`, `t_80`, `overshoot_c`, `settle_sd` per run, the two blocks side by side, A2 in run order so drift reads left to right |
| Phase B: minutes to target by cell | bars with the individual runs as points; the 10 % baseline shown as a fifth point |
| Effects | table from `sauna_model`: each factor in minutes with its interval; best cell and whether it is settled |
| Runs | `sauna_runs` newest first; invalid rows dimmed with their reason; adaptation runs marked |
| Progress | phase, valid runs done / planned, next setup |
| Start temperature vs minutes to target | scatter with the fitted line — the scheduling estimate, and why the model adjusts for start temperature |

The existing *Sauna and Sprinklers* dashboard keeps its live panels; this one is history.

### Payoff: ready-at scheduling

With the heat-up estimate in hand, `sauna.py schedule` gains `--ready-at HH:MM`: start = ready
time − predicted `t_target` for the sauna's temperature right now (the last gauge) in the chosen
setup. "Schedule the sauna to be ready at 7" in Slack, and later over Alexa
([plan-alexa.md](plan-alexa.md)), becomes exactly that.

## Not in this plan

- **Energy.** No meter on a 9 kW circuit and a smart plug cannot go there; a mode's energy claim
  is not testable here, only its time, overshoot and steadiness. A CT clamp on the heater's
  breaker is a separate small project if the answer matters.
- **A second target temperature** or a different probe position: both are follow-ups on the same
  machinery. The probe stays where it is for the whole experiment.

> **Built 2026-10-15** (Robby moved it up from Sunday). Live: `iot-poll.py --poll-huum` every
> minute with `sauna_runs` / `sauna_samples` (migration 005, applied by apply.sh), the raw
> status payload logged once per session to `~/logs/iot-poll.log`, `sauna.py next | label |
> runs | phase` with the plan in `~/logs/sauna-plan.json`, the four `sauna-*` verbs in
> `openclaw-cmd`, the forge wrapper and allowlist, the `sauna` skill, and the *Sauna heat-up*
> dashboard. Tested against the database with a synthetic 95-minute session (removed after).
> Still to do: `sauna-analysis.py` (the effects table is empty until then; the dashboard's
> per-setup bars and phase A table need no model), `--ready-at`, and the verb grant in
> bastion's `authorized_keys`, which is done by hand. The experiment begins with `phase begin`.

> **Runs 1–2 (2026-10-15/16), and two poller faults found by them.** Run 1: 25 °C start, 62 min
> to 80, 76 to 91, 1 °C overshoot. Run 2: 28 °C start, 61 to 80, 78 to 91, no overshoot. Both
> simple, bench 0 %, ceiling 10 %. Faults: (1) the minute and five-minute cron polls shared one
> state file and raced at 18:50, opening run 1 twice -- each poll now writes back only its own
> section; (2) after HUUM's cloud outage at 23:52 the controller reported status 230 "offline"
> for the entire morning session while its readings kept updating, so the poller saw no session
> and run 2 had to be reconstructed from Prometheus (15 s scrapes of the minute gauges) -- a
> session the API still describes (target set, end in the future) now counts as heating under
> 230 too, and a warm start never consumes the pending label. The API shows no heating-mode
> field while heating either (`config: 0, duration, startDate, endDate, targetTemperature`).
>
> **Phase A1 complete 2026-10-16** (simple: 62/76, 61/78, 60/72 min to 80/91 from 25/28/32 °C;
> thermostat hold sags 2–5 °C). **A2 (learning) runs 1–2** on 10-17/18: 63/79 from 25 °C and
> 62/77 from 29 °C, 1 °C overshoot, hold sd 0.3/0.5 -- same heat-up, far steadier hold, no
> drift yet. The controller stayed "offline" to HUUM's cloud from the 10-15 23:52 outage until
> Robby **power-cycled it 2026-10-18 ~17:40** (status back to 232; the cloud rejected starts with
> HTTP 400 meanwhile). Learning runs 3–6 are after the power cycle; the controller asks for a
> confirmation before clearing learned data, so it should be unaffected -- watch run 3 anyway.

## Decisions before Sunday

1. **Which mode is set today** — that is block A1, and it is also what the 26 baseline runs
   measured.
2. **Slider reading:** confirm "closed / 10 % / fully open" are positions you can set and
   reproduce by eye (a mark on the slider is enough). The labels are percentages.
3. **Nothing else.** The target (91), the room (stable) and the probe (bench) are settled by
   your answers; the weather lookup is gone from the plan.

## Steps

0. **[agent, before Sunday]** Capture a raw HUUM status payload during one heating session to
   see whether the mode shows up in the API (nothing while idle). *Baseline table: done, above.*
1. **[you, Sunday]** Decision 1 and 2.
2. **[agent]** `005-sauna.sql`; `iot-poll.py --poll-huum` with run and sample recording;
   crontab lines; `--dry-run` shown before deploy. **[you]** apply the migration with psql.
3. **[agent]** `sauna.py next | label | runs | phase`, the plan file, `exec-approvals` entries,
   the `sauna` skill lines. Deploy forge for the skill, bastion for the scripts.
4. **[you]** That evening: ask Slack for the next run, set and check the sliders, start as
   usual. **[agent]** Next morning: confirm run 1 has one-minute samples, its label, a start
   temperature, and the right `t_80`/`t_target` against the old 5-minute figure.
5. **[agent, during the week]** `sauna-analysis.py` and the dashboard; first useful after A1's
   three runs, needed by the end of A2 (about nine evenings in).
6. **[you]** `sauna.py phase decide …` after A2 — switch the panel to the winner if it is the
   other one. **[agent]** writes the phase B plan.
7. **[both]** After phase B: read the effects table; settled → the answer goes at the top of this
   file and becomes the default setup in the skill text; tied → two more of each of the top
   two. Optional phase C on the ceiling slider. Then `--ready-at`.
