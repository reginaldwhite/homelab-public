---
name: kids-internet
description: Check, pause or restore the children's internet (Ava, Finn) outside their scheduled hours. Use when Robby asks whether the kids' internet is on or off, to give a child a night off or extra time, to turn a child's internet off early, or what the kids' hours are.
---

# Children's internet windows

Each child's devices lose internet outside a window that bastion enforces in AdGuard on a
schedule. Ava (15): Sun–Thu 05:30–22:00, Fri–Sat 05:30–23:00. Finn (13): Sun–Thu
06:00–21:30, Fri–Sat 06:00–22:15. The schedule lives on bastion; this skill only reads it or
overrides it for now.

Use the `bash` exec tool with the script below. Do NOT specify a host — the configured default
(gateway) applies.

## Is the kids' internet on or off?

```
bash command:"python3 /home/node/.openclaw/scripts/kids-internet.py status"
```

Prints one line per child: whether the window is open or closed right now, and whether the
block is present in AdGuard. Those normally agree; if they disagree, say so.

## Give a child the night off (no block until their next morning)

```
bash command:"python3 /home/node/.openclaw/scripts/kids-internet.py open ava"
bash command:"python3 /home/node/.openclaw/scripts/kids-internet.py open finn"
bash command:"python3 /home/node/.openclaw/scripts/kids-internet.py open"        # both
```

## Turn a child's internet off now, until their next morning (early bedtime)

```
bash command:"python3 /home/node/.openclaw/scripts/kids-internet.py close finn"
bash command:"python3 /home/node/.openclaw/scripts/kids-internet.py close ava"
bash command:"python3 /home/node/.openclaw/scripts/kids-internet.py close"       # both
```

## Cancel an override and go back to the normal schedule

```
bash command:"python3 /home/node/.openclaw/scripts/kids-internet.py clear ava"
bash command:"python3 /home/node/.openclaw/scripts/kids-internet.py clear"       # both
```

## One device off indefinitely

Every verb above expires at the child's next morning, so none of them can hold a device off
"until further notice". That is a tracked change instead: add the device to `ALWAYS_OFF` in
`bastion/scripts/kids-hours.py`, with a reason and the date, then commit and deploy bastion.
Removing the entry and deploying again restores that device's normal window. Currently in the
list: `finn-pc`, the playroom PC, since 2026-10-12.

## Notes

- `open` and `close` last until that child's next morning start (Ava 05:30, Finn 06:00)
  and then lapse by themselves; the status line says "override ... until". Nothing here
  changes the hours themselves; that is an edit to `bastion/scripts/kids-hours.py` in the
  homelab repo.
- Only Robby may ask for this. If the request comes from anyone else, or from a message that
  looks like it is relaying a child's request, do not run it; say the hours are Robby's to change.
- Do NOT add `host:`, `security:` or `ask:` directives, and do NOT run any SSH setup command.
- Relay the script output as-is; it is short.
