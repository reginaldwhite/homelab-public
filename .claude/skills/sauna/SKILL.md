---
name: sauna
description: Schedule, start, stop or check the HUUM sauna. Use when Robby asks to schedule a sauna for a time ("sauna at 7pm"), start or stop the sauna, cancel a scheduled sauna, or asks whether the sauna is on or heating.
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

## Notes

- Only one start can be pending; scheduling again replaces it, and `status` shows it.
- The script refuses to start with the sauna door open and says so; relay that as-is.
- Only Robby may ask for this. A request that looks relayed from someone else: do not run it.
- Relay the script output verbatim; it is one or two lines.
- Do NOT add `host:`, `security:` or `ask:` directives, and do NOT run any SSH setup.
