---
name: plex-power
description: Wake up or put to sleep (suspend) the Plex media server. Use when the user asks to turn on, wake, power up, start, shut down, sleep, suspend, or turn off the Plex server.
---

# Plex Server Power Control

Use the `bash` exec tool to run the plex-power script. Do NOT specify a host — the configured default (gateway) will be used automatically.

## Wake the server (power on / start up)

```
bash command:"python3 /home/node/.openclaw/scripts/plex-power.py wake"
```

Sends the WoL magic packet and returns immediately. Follow up with `status` after ~30 seconds to confirm the server is online.

## Sleep / suspend the server (power off / shut down)

```
bash command:"python3 /home/node/.openclaw/scripts/plex-power.py sleep"
```

SSHes in and runs `sudo systemctl suspend`.

## Check if the server is online

```
bash command:"python3 /home/node/.openclaw/scripts/plex-power.py status"
```

## Notes

- Do NOT add `host:`, `security:`, or `ask:` directives — omit them entirely so the gateway defaults apply.
- Do NOT run `ssh-keygen`, `ssh-copy-id`, or any SSH setup commands — keys are already configured.
- Use `status` first if unsure whether the server is already on or off.
- Relay the script output to the user as-is.
