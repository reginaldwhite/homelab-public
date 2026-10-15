# Plan: sauna heat-up experiment — which vents, which heating mode, how long

Written 2026-10-15; implementation starts Sunday 2026-10-18. Builds on the HUUM poller and
`sauna.py` from [plan-kids-hours-and-iot.md](plan-kids-hours-and-iot.md) (Part 2).

## The question

The sauna has one fixed intake by the heater and two adjustable exhaust vents, one under the
top bench and one in the ceiling. The heater offers a *standard* and a *smart* heating cycle.
Robby wants to know, with numbers rather than a feeling:

1. **Which combination of vent positions and heating mode reaches the target fastest.**
2. **How long heating takes**, well enough that "sauna at 7" can mean *ready* at 7, not *switched
   on* at 7.

The hunch about the modes is that *standard* runs the elements flat out until the target and
*smart* eases off as it approaches. That is unverified: the HUUM API exposes no mode field
while idle (`maxHeatingTime, statusCode, door, config, temperature, remoteSafetyState`), so step
0 captures a payload while heating to see whether one appears. Either way, the temperature
curve itself will show it — an easing-off mode shows a slope that flattens in the last 10 °C
and a smaller overshoot, and that is measurable.

## What the data already says

Prometheus has held `huum_temperature_celsius` at 5-minute resolution since 2026-09-27, so a
baseline exists without doing anything. Reconstructed from the 26 heating intervals in the last
18 days (cold evening starts only; the short second sessions of an evening excluded):

| | cold start 23–27 °C, target 93 | notes |
|---|---|---|
| minutes to 80 °C | **60–65** | every run within that band |
| minutes to target | **80–90** | 5-minute resolution, so ±5 |
| peak | **95–98** (target 93) | 3–5 °C of overshoot; smart mode may be about this |
| 82 °C target (2026-10-13) | 60 to target | |

Three points this settles about the experiment design:

- **The process is very repeatable.** With the same vents and mode (whatever they were), run-to-
  run spread is about ±5 minutes at the current resolution. A 10-minute difference between
  combinations will therefore be obvious with two replicates each; a 5-minute one needs the
  finer sampling below and three.
- **Start temperature is the big covariate.** A 61 °C start reached target in 50 minutes against
  85 from 25 °C. Evening cold starts sit at 23–33 °C today, and that band will move down with
  the weather. Every run must record its start temperature and the model must adjust for it.
- **5-minute polling is too coarse** for the question. Reaching target is a single sample
  crossing, so the measurement error is the poll interval. One-minute sampling while heating
  cuts that to ±1.
- Several evenings show the temperature stalling at 91 and never touching 93 — that is the door
  opening when the session starts, not the heater. Runs are scored on the *heat-up*, which ends
  at the first door opening or the first target crossing, whichever is first.

## Factors and responses

**Factors** (three, two levels each → 8 cells):

| factor | levels | set where |
|---|---|---|
| bench vent | closed / open | by hand, before starting |
| ceiling vent | closed / open | by hand, before starting |
| heating mode | standard / smart | on the controller or in the HUUM app, before starting |

Vents are tested fully open versus fully closed. If they have detents or a half position, that
is a second experiment once the full-open/closed answer exists.

**Held fixed:** target temperature (one value for the whole experiment; 91 or 93, decide below),
door closed throughout the heat-up, a cold start (first sample under 40 °C — a second session of
the evening is never a run), no steamer.

**Recorded covariates:** start temperature (first sample), outdoor temperature and wind at the
start (Open-Meteo, free, no key, hourly for a latitude/longitude), hour of day, date.

**Responses**, per run, all computed from one-minute samples:

| name | definition | what it answers |
|---|---|---|
| `t_80` | minutes from heating start to first sample ≥ 80 °C | "usable" time; robust to mode differences near the target |
| `t_target` | minutes to first sample ≥ target | the primary response |
| `overshoot_c` | max temperature in the 30 min after `t_target`, minus target | what smart mode probably changes |
| `tail_slope` | °C/min over the last 10 °C before target, divided by the slope over 50→70 °C | the curve's knee; < 0.7 means the heater eased off |
| `settle_sd` | standard deviation of temperature from 30 to 60 min after target, door closed | how steady it holds, if the session gets that far |

## Experimental design

A **2³ full factorial with two replicates: 16 runs**, which at roughly one sauna an evening is
about three weeks. The order is randomised, with one constraint: runs 1–8 are a complete
replicate (every cell once) and runs 9–16 are the second. If the experiment stops early at 8,
every cell has still been seen once; if two cells are within each other's confidence interval
after 16, a third replicate of just those two decides it.

The script picks the order (seeded, so it is reproducible) and tells Robby what to set next;
nobody chooses a cell by mood, which is what makes the weather and the calendar average out
instead of lining up with a factor.

**A run is invalid**, and its cell goes back in the queue, when: the door opens before target;
the start temperature is 40 °C or over; the target differs from the fixed one; the controller
goes offline mid-heat-up (`statusCode 230` — the 2026-10-07 sessions); or the label was never
set. Invalid runs are kept in the table, flagged, and excluded from the model, never deleted.

## Analysis

Two views, both on the dashboard:

1. **Per cell, plainly:** mean `t_target` and `t_80` with the individual runs shown, so the
   picture needs no statistics to read.
2. **A fitted model**, because start temperature and weather differ between runs and two
   replicates per cell are few. Least squares on

   `t_target = β0 + β1·bench + β2·ceiling + β3·mode + β4·bench×ceiling + β5·bench×mode + β6·ceiling×mode + β7·start_c + ε`

   (the three-way interaction is dropped; the outdoor temperature is tried as β8 and kept only
   if it earns its place). Sixteen runs against eight coefficients leaves eight degrees of
   freedom — enough for a t-based 95 % interval on each effect. The output is a table in minutes:
   *closing the ceiling vent saves 9 min (95 % CI 4–14)*, and a predicted time per cell at a
   reference start of 25 °C. The best cell is the one with the lowest prediction; it is
   "determined" when its interval does not overlap the runner-up's, and otherwise the honest
   answer is "these two are the same, pick by comfort".

   The same model is fitted to `t_80` and `overshoot_c`. The fit is a 8×8 normal-equations
   solve in pure Python (bastion has no numpy and does not need it for this).

**Reading the heating-mode hypothesis:** if smart mode shows a lower `tail_slope` and smaller
`overshoot_c` at the cost of a longer `t_target` but a similar `t_80`, the hunch is confirmed
and the choice is "do you want the last 10 °C fast, or no overshoot". If the curves are
indistinguishable, the mode is cosmetic and the experiment collapses to four cells.

## What gets built

### Data capture (the part that must land on Sunday; everything else can follow while runs accumulate)

- **One-minute HUUM polling.** `iot-poll.py` gains `--poll-huum` for a `* * * * *` cron line;
  the existing `*/5` line keeps Rachio (its 3 500/day budget is why the two split). HUUM's
  status is one small JSON a minute, 1 440 a day; Home Assistant polls it faster than that.
  The 5-minute gauges and the `sauna/session` event are unchanged.
- **Two tables**, migration `bastion/observability/postgres/005-sauna.sql`, applied by hand with
  psql as the file's header says:
  - `sauna_runs` — `id, started_at, ended_at, target_c, bench_vent, ceiling_vent, mode, start_c,
    outdoor_c, wind_kph, t_80_min, t_target_min, overshoot_c, tail_slope, settle_sd, door_opened_at,
    valid, invalid_reason, plan_seq, note`. One row per heating session, labelled or not; unlabelled
    sessions outside the experiment still get their numbers, which keeps the heat-up estimate
    improving after the experiment ends.
  - `sauna_samples` — `run_id, ts, temp_c, target_c, door_closed, status_code`, one row a minute
    while heating. Small: a 3-hour session is 180 rows.
- **Run lifecycle in the poller:** heating seen → insert a run (start temperature from this
  sample, Open-Meteo lookup, pending label attached if one is set); each minute → a sample; door
  opens or target crossed → freeze the heat-up responses; heating stops → close the run and
  compute the rest. State lives in the existing `iot-state.json`.
- **Labels and the plan, in `sauna.py`:**
  ```
  sauna.py next                          # prints the next cell to set up, e.g.
                                         #   run 5 of 16: bench CLOSED, ceiling OPEN, mode SMART — label set
  sauna.py label bench=open ceiling=closed mode=standard   # label by hand instead
  sauna.py label clear
  sauna.py runs                          # the table so far, one line a run
  ```
  A label is "pending" until a session starts, then attaches to that run; `next` both prints
  and sets it. The randomised order lives in `~/logs/sauna-plan.json`, created on first `next`
  with a fixed seed. These verbs are read-mostly and get `exec-approvals` entries and lines in
  the `sauna` skill, so "what's the next sauna experiment" works from Slack; the agent still
  cannot change vents, so the skill says to relay the line and nothing else.
- **Weather:** Open-Meteo's forecast endpoint with the sauna's coordinates (`SAUNA_LAT`/`SAUNA_LON`
  in bastion's `.env`), current temperature and wind, fetched once at run start; a failure
  leaves the columns null rather than blocking the run.

### Analysis

`bastion/scripts/sauna-analysis.py`, run from cron an hour after each session ends (or daily at
06:50 — simpler, and nothing is lost): fits the model above to the valid labelled runs, writes
the effect table and per-cell predictions to a small `sauna_model` table (one row per term per
response, with estimate, low, high, n, fitted_at), and prints the same to stdout so it can be
read from a shell. It also updates the plain heat-up estimate used for scheduling:
`t_target` as a function of start temperature over *all* valid runs, labelled or not.

### Dashboard — *Sauna heat-up* (`grafana/dashboards/sauna-heatup.json`, uid `homelab-sauna-heatup`)

| panel | source | shows |
|---|---|---|
| Heat-up curves | XY chart on `sauna_samples` joined to runs, x = minutes since start, one line per run coloured by cell | the whole experiment at a glance; the smart-mode knee if it exists |
| Minutes to target by cell | bar chart with the individual runs as points | the plain answer |
| Effects | table from `sauna_model` | each factor in minutes with its interval; best cell and whether it is settled |
| Runs | table from `sauna_runs`, newest first, invalid rows dimmed with their reason | what has been done, what is queued |
| Progress | stat: valid runs / 16, next cell | |
| Start temperature vs minutes to target | scatter, all runs, the fitted line | why the model adjusts for it |
| Overshoot and tail slope by mode | two small bars | the mode hypothesis |

The existing *Sauna and Sprinklers* dashboard keeps its live panels; this one is history and
analysis.

### Payoff: ready-at scheduling

Once the heat-up estimate exists, `sauna.py schedule` gains `--ready-at HH:MM`: start time =
ready time − predicted `t_target` for the sauna's temperature right now (read from the last
gauge) under the chosen cell. "Schedule the sauna to be ready at 7" in Slack, and later over
Alexa ([plan-alexa.md](plan-alexa.md)), becomes exactly that.

## Not in this plan

- **Energy.** There is no power meter on a 9 kW circuit and a smart plug cannot go there; smart
  mode's energy claim is not testable here, only its time and overshoot. A CT clamp on the
  heater's breaker is a separate small project if the answer matters.
- **Bench temperature.** The controller's sensor is high on the wall; the experiment optimises
  what *it* reads. A second sensor at bench height (a Zigbee or BLE thermometer and a bridge on
  bastion) would show whether a vent combination that is fastest at the sensor is also warmest
  on the bench. Worth doing after, not before.
- **Partial vent positions** and a second target temperature: follow-up experiments on the
  same machinery.

## Decisions before Sunday

1. **Vents:** are they open/closed only, or sliders with positions? (The plan assumes two
   positions; a slider is still tested at its ends.)
2. **Target for the experiment:** 91 or 93 °C. Recent sessions used both; the model cannot
   compare across targets, so one value for the whole run of 16.
3. **Where the heating mode is set** (controller panel or app) and what the app calls the two
   options, so the labels use the real names.
4. **Coordinates** for the weather lookup (two decimals is plenty) — and confirmation that the
   sauna is a separate building, which is why outdoor temperature is in the model at all.
5. **Starting the sessions:** panel, app or Slack, any of them; the only rule is `sauna.py next`
   (or a Slack "next sauna experiment") *before* setting the vents, so the label is pending when
   the session starts.

## Steps

0. **[agent, before Sunday]** Capture a raw HUUM status payload during one heating session to
   see whether the mode is visible. Reconstruct the baseline table above from Prometheus into a
   one-off note in this file (done: the table in *What the data already says*).
1. **[you, Sunday]** Answer the five decisions; put `SAUNA_LAT`/`SAUNA_LON` in bastion's `.env`.
2. **[agent]** `005-sauna.sql`; `iot-poll.py --poll-huum` with run and sample recording;
   crontab lines; `--dry-run` shown before deploy. **[you]** apply the migration with psql.
3. **[agent]** `sauna.py next | label | runs`, the seeded plan, `exec-approvals` entries, the
   `sauna` skill lines. Deploy forge for the skill, bastion for the scripts.
4. **[you]** That evening: `sauna.py next` (or ask in Slack), set vents and mode, start as
   usual. **[agent]** Next morning: confirm run 1 has 1-minute samples, a label, a start
   temperature and weather, and the right `t_80`/`t_target`.
5. **[agent, during the week]** `sauna-analysis.py` and the dashboard; both useful from about
   run 8, so there is no rush before then.
6. **[both]** After run 16: read the effects table. Settled → write the answer at the top of this
   file and set the cell as the default in the skill text. Two cells tied → runs 17–20 on those
   two. Then `--ready-at`.
