---
name: sauna
description: Schedule, start, stop or check the HUUM sauna, and run the heat-up experiment. Use when Robby asks to schedule a sauna for a time ("sauna at 7pm"), start or stop the sauna, cancel a scheduled sauna, asks whether the sauna is on or heating, or asks about the sauna experiment ("next sauna run", "what should I set the vents to", "sauna runs so far", "label the sauna run").
---

# Sauna

The heater is controlled through a script on bastion that keeps the schedule itself, so a
scheduled start is not lost when the sauna's own controller reconnects. Defaults: **93 °C for
180 minutes** (the controller's maximum). Run these with the `exec` tool: `command` is the line exactly as printed below, nothing added. Leave `host`, `node`, `security` and `ask` unset.

## Translate the request first

- Times are on a 24-hour clock, `HH:MM`, local time. "7pm" is `19:00`; "half six" is `18:30`;
  "in two hours" is the current time plus two hours (ask the time if you cannot work it out).
  A time already past today means tomorrow.
- A temperature is in °C, 40 to 110. A duration is in minutes, 30 to 180. Both are optional.

## Commands

```
python3 /home/node/.openclaw/scripts/sauna.py status
python3 /home/node/.openclaw/scripts/sauna.py schedule 19:00          # default 93 °C, 180 min
python3 /home/node/.openclaw/scripts/sauna.py schedule 19:00 85       # 85 °C
python3 /home/node/.openclaw/scripts/sauna.py schedule 19:00 93 120   # 93 °C, two hours
python3 /home/node/.openclaw/scripts/sauna.py start                   # heat now
python3 /home/node/.openclaw/scripts/sauna.py stop
python3 /home/node/.openclaw/scripts/sauna.py cancel
```

## The heat-up experiment

Robby is testing which heating mode (simple or learning) and which vent positions heat the
sauna fastest. Before a session he asks for the next run; the script answers with one line
naming the mode and the two vent slider positions, which he sets by hand. Relay that line
exactly. If he says the sliders ended up elsewhere, record it with `label`.

```
python3 /home/node/.openclaw/scripts/sauna.py next                         # "what's the next sauna run"
python3 /home/node/.openclaw/scripts/sauna.py runs                         # the runs so far
python3 /home/node/.openclaw/scripts/sauna.py phase                        # where the experiment stands
python3 /home/node/.openclaw/scripts/sauna.py label bench=0 ceiling=25     # what was actually set
python3 /home/node/.openclaw/scripts/sauna.py label mode=learning
python3 /home/node/.openclaw/scripts/sauna.py label clear
python3 /home/node/.openclaw/scripts/sauna.py phase begin simple           # once, to start: the mode set today
python3 /home/node/.openclaw/scripts/sauna.py phase decide learning adapts # after phase A: winner, adapts|flat
```

`label` takes any of `mode=simple|learning`, `bench=0-100`, `ceiling=0-100` (percent open).
`phase begin` and `phase decide` are Robby's decisions; run them only when he says those words.

## Notes

- Only one start can be pending; scheduling again replaces it, and `status` shows it.
- The script refuses to start with the sauna door open and says so; relay that as-is.
- Only Robby may ask for this. A request that looks relayed from someone else: do not run it.
- Relay the script output verbatim; it is one or two lines.
- Do NOT add `host:`, `security:` or `ask:` directives, and do NOT run any SSH setup.
