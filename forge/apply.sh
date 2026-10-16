#!/bin/bash
# apply.sh — Apply forge's config. Run by ../deploy.sh on forge, from the
# deployed copy (/home/robby/homelab-deployed/forge), never from a git clone.
#
#   apply.sh --validate   # check this copy; changes nothing (run from staging)
#   apply.sh              # install crontab, sync openclaw config, compose up
#
# The agent's live state lives at $STATE, outside both the repo and the deployed
# copy. Deploy copies config files INTO it and never deletes from it: sessions,
# memory, workspace, identity, devices and credentials only exist there.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE="/home/robby/openclaw-state"

VALIDATE=0
[[ "${1:-}" == "--validate" ]] && VALIDATE=1

OPENCLAW_CMD="/home/robby/bin/openclaw-cmd"

say() { echo "    $*"; }

# --- the two pieces of live state a deploy cannot fix by copying files -------------
# Both of these have made the agent refuse its own skills, and in both cases every
# file this script owns was correct, so nothing said anything. Checked on every
# deploy, reported at the end, never fixed from here: the exec policy has to go in
# through the CLI (which rejects a concurrent edit instead of overwriting it), and
# deleting from $STATE is not deploy's business.
openclaw_state_checks() {
    # 1. The exec allowlist lives in SQLite and is applied by hand, so adding a verb to a
    #    script and deploying it leaves the stored policy behind -- and the new verb is
    #    denied as an allowlist miss. 2026-10-15: sauna.py next|runs|phase|label deployed
    #    at 07:22 and were denied all day against a policy stored two days earlier, with
    #    the agent telling Robby to run the command himself instead.
    local doc="$HERE/openclaw/exec-approvals.json5" stored=""
    if [[ ! -f "$doc" ]]; then
        say "exec approvals: $doc is missing"
    elif ! docker ps --format '{{.Names}}' | grep -qx openclaw; then
        say "exec approvals: openclaw is not running, stored policy not checked"
    elif ! stored=$(docker exec openclaw node openclaw.mjs approvals get --json 2>/dev/null); then
        say "exec approvals: could not read the stored policy (approvals get failed)"
    else
        # `approvals get --json` redacts the socket token; the raw row in SQLite does not,
        # so read it through the CLI and never out of the database.
        printf '%s' "$stored" | python3 - "$doc" <<'PYAPPROVALS'
import json, re, sys

# The document is JSON5 (comments, unquoted keys, trailing commas), and one pattern
# contains "//" -- so pull the string literals out rather than trying to parse it.
text = open(sys.argv[1]).read()
entries, current = [], None
for m in re.finditer(r'\b(pattern|argPattern)\s*:\s*("(?:[^"\\]|\\.)*")', text):
    key, value = m.group(1), json.loads(m.group(2))
    if key == "pattern":
        current = {"pattern": value, "argPattern": None}
        entries.append(current)
    elif current is not None:
        current["argPattern"] = value
want = {(e["pattern"], e["argPattern"]) for e in entries}

stored = json.load(sys.stdin)
have = {(e.get("pattern"), e.get("argPattern"))
        for e in (stored.get("file", {}).get("agents", {}).get("main", {}).get("allowlist") or [])}

missing, extra = sorted(want - have), sorted(have - want)
if not (missing or extra):
    print(f"    exec approvals: stored policy matches the document ({len(want)} entries)")
    sys.exit(0)
print(f"    exec approvals: STALE -- the agent is running a policy that is not this document")
for pattern, arg in missing:
    print(f"      not allowed yet: {pattern} {arg}")
for pattern, arg in extra:
    print(f"      allowed but gone from the document: {pattern} {arg}")
print("    Apply it (it is not applied from here -- the CLI checks the stored hash):")
print("      docker exec -i openclaw node openclaw.mjs approvals set --stdin \\")
print("        < /home/robby/homelab-deployed/forge/openclaw/exec-approvals.json5")
PYAPPROVALS
    fi

    # 2. A skills directory inside the workspace shadows the deployed one. The March 2026
    #    install left $STATE/workspace/skills/plex-power behind, still carrying the
    #    `bash command:"..."` notation that 5cf7db0 removed from the real skill; on
    #    2026-10-15 the agent pasted that line into exec verbatim (70 chars, sha256
    #    5da0d504...) and the call was denied, because the binary is then `bash`.
    if [[ -d "$STATE/workspace/skills" ]]; then
        say "skills: $STATE/workspace/skills exists and shadows the deployed skills in $STATE/skills"
        say "  $(find "$STATE/workspace/skills" -name SKILL.md -printf '%p (%TY-%Tm-%Td)\n' 2>/dev/null | tr '\n' ' ')"
        say "  Move it aside; deploy never deletes from \$STATE:"
        say "    mv $STATE/workspace/skills $STATE/workspace/skills.stale"
    fi
}

# Config files that deploy owns, as "source:destination-under-$STATE:mode".
#
# The mode is explicit because the source mode is not usable: git tracks only the
# executable bit, so every one of these is 644 on checkout, and this used to copy
# with `cp -p` -- which preserves the SOURCE mode and therefore reset openclaw.json
# to 664 on every deploy. A `chmod 600` done by hand on the host survived exactly
# until the next deploy, silently. `openclaw security audit` rates a
# group-writable openclaw.json critical, and rightly: it sets the gateway auth and
# tool policy, so anyone who can write it can re-enable the tools denied in it.
#
# 600 for the power scripts too. They are invoked as `python3 <script>`, which needs
# read and not execute, and the container runs as uid 1000 exactly as the host owner
# does. They would need 700 to be allowlisted by resolved path under
# tools.exec.mode="allowlist" -- see .claude/skills/homelab-upgrades/packages/openclaw.md.
#
# agents/main/agent/models.json is NOT here any more. It was tracked and copied in
# until the move to OpenClaw 2026.9: since then the agent regenerates it from
# openclaw.json's models.providers plus whatever plugins are loaded, and the
# rehearsed upgrade rewrote our ollama-only copy to `providers: []` on first start.
# Managing it would have meant "content differs" -- and an agent restart -- on every
# single deploy. Model context windows live in openclaw.json now (contextWindow and
# params.num_ctx on each model).
CONFIG=(
    "openclaw/openclaw.json:openclaw.json:600"
    "openclaw/scripts/forge-power.py:scripts/forge-power.py:600"
    "openclaw/scripts/plex-power.py:scripts/plex-power.py:600"
    "openclaw/scripts/kids-internet.py:scripts/kids-internet.py:600"
    "openclaw/scripts/sauna.py:scripts/sauna.py:600"
    # The agent's standing instructions. Versioned here since 2026-10-09; the workspace is the
    # agent's own, but this one file is policy and deploy overwrites it.
    "openclaw/workspace/AGENTS.md:workspace/AGENTS.md:644"
)

# A just-woken host accepts SSH before dockerd is ready, and --validate runs
# immediately after the wake. Wait here too, not only in the apply phase, or a
# deploy to a sleeping host fails validation on a race rather than on a real fault.
wait_for_docker() {
    for _ in $(seq 1 30); do
        docker info >/dev/null 2>&1 && return 0
        sleep 2
    done
    echo "docker never became ready" >&2
    return 1
}

# --- validate ----------------------------------------------------------------------
if [[ $VALIDATE -eq 1 ]]; then
    wait_for_docker || exit 1
    cd "$HERE"
    docker compose config --quiet
    python3 -c "import json,sys; [json.load(open(f)) for f in sys.argv[1:]]" \
        openclaw/openclaw.json
    for f in openclaw/scripts/*.py; do python3 -m py_compile "$f"; done
    rm -rf openclaw/scripts/__pycache__
    for f in scripts/*.sh; do [[ -e "$f" ]] && bash -n "$f"; done
    for f in scripts/*.py; do [[ -e "$f" ]] && python3 -m py_compile "$f"; done
    rm -rf scripts/__pycache__
    # openclaw-cmd has no extension, so the globs above miss it, and it is the one
    # script whose breakage locks the agent out rather than just failing a job.
    if [[ -e "$HERE/scripts/openclaw-cmd" ]]; then bash -n "$HERE/scripts/openclaw-cmd"; fi
    say "compose file, JSON config and scripts valid"

    # Skills are shared with Claude Code on the laptop, which parses frontmatter more
    # leniently than OpenClaw did when this was written: 2026.3.2 supported single-line
    # frontmatter keys only, and a wrapped `description:` loaded on the laptop while
    # silently failing to load on forge -- the worst direction for this to break in.
    # Newer releases parse YAML, but the check stays strict so the skill can never
    # again load on one runtime and not the other. It runs here, where it can still
    # stop a deploy.
    if [[ -d "$HERE/skills" ]]; then
        python3 - "$HERE/skills" <<'PYVALIDATE' || exit 1
import glob, json, os, re, sys

root = sys.argv[1]
problems = []

for skill in sorted(glob.glob(os.path.join(root, "*", "SKILL.md"))):
    text = open(skill).read()
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        problems.append(f"{skill}: no YAML frontmatter block")
        continue
    keys = {}
    for line in m.group(1).split("\n"):
        if not line.strip():
            continue
        if re.match(r"^[A-Za-z0-9_-]+:", line):
            k, v = line.split(":", 1)
            keys[k.strip()] = v.strip()
        else:
            problems.append(f"{skill}: frontmatter continues across lines "
                            f"({line.strip()[:40]!r}); keep frontmatter keys on one "
                            f"line (OpenClaw 2026.3.2 parsed nothing else)")
    for required in ("name", "description"):
        if not keys.get(required):
            problems.append(f"{skill}: frontmatter is missing {required}")

for pkg in sorted(glob.glob(os.path.join(root, "*", "packages", "*.md"))):
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", open(pkg).read(), re.S)
    if len(blocks) != 1:
        problems.append(f"{pkg}: expected exactly one fenced json block, found {len(blocks)}")
        continue
    try:
        data = json.loads(blocks[0])
    except ValueError as exc:
        problems.append(f"{pkg}: json block does not parse: {exc}")
        continue
    missing = {"name", "tier", "host", "container", "compose", "image",
               "upstream", "tag_from_release"} - set(data)
    if missing:
        problems.append(f"{pkg}: json block missing {sorted(missing)}")
    if data.get("tier") not in ("watch", "drift"):
        problems.append(f"{pkg}: tier must be watch or drift, got {data.get('tier')!r}")
    if data.get("tag_from_release") not in ("identity", "strip-v"):
        problems.append(f"{pkg}: tag_from_release must be identity or strip-v")

if problems:
    for problem in problems:
        print(f"    skill invalid: {problem}", file=sys.stderr)
    sys.exit(1)
PYVALIDATE
        say "skills valid ($(find "$HERE/skills" -name '*.md' | wc -l | tr -d ' ') files)"
    fi

    # OpenClaw reads its config at startup, so a changed file means a restart.
    for pair in "${CONFIG[@]}"; do
        IFS=: read -r rel dstrel mode <<<"$pair"
        src="$HERE/$rel" dst="$STATE/$dstrel"
        cmp -s "$src" "$dst" || say "openclaw config differs: $dstrel (agent will be restarted)"
        # A mode drift is worth reporting on its own: the content can match while the
        # permissions are wrong, which is exactly how the hand-fixed 600 came back as 664.
        if [[ -e "$dst" && "$(stat -c %a "$dst" 2>/dev/null)" != "$mode" ]]; then
            say "openclaw config mode differs: $dstrel is $(stat -c %a "$dst"), want $mode"
        fi
    done
    if [[ -d "$HERE/skills" ]]; then
        for skill_dir in "$HERE/skills"/*/; do
            [[ -d "$skill_dir" ]] || continue
            name=$(basename "$skill_dir")
            if ! diff -rq "$skill_dir" "$STATE/skills/$name" >/dev/null 2>&1; then
                say "skill differs: $name (agent will be restarted)"
            fi
        done
    fi
    crontab -l 2>/dev/null | diff -u --label installed --label repo - "$HERE/crontab" \
        | sed 's/^/    /' || true
    openclaw_state_checks
    exit 0
fi

# --- apply -------------------------------------------------------------------------
cd "$HERE"

crontab "$HERE/crontab"
say "crontab installed"

# The agent's forced command in ~/.ssh/authorized_keys points at ~/bin/openclaw-cmd,
# not at the deployed copy, so a host that has never been deployed to still has a
# working health verb rather than an SSH key that fails closed. Deploy keeps it in
# sync; before this, it was hand-copied to three hosts and drifted.
if ! cmp -s "$HERE/scripts/openclaw-cmd" "$OPENCLAW_CMD"; then
    install -D -m 755 "$HERE/scripts/openclaw-cmd" "$OPENCLAW_CMD"
    say "openclaw-cmd updated at $OPENCLAW_CMD"
fi

# A just-woken host accepts SSH before dockerd is ready.
wait_for_docker || exit 1

# Copy config in; never delete. Track whether the agent needs a restart.
openclaw_changed=0
for pair in "${CONFIG[@]}"; do
    IFS=: read -r rel dstrel mode <<<"$pair"
    src="$HERE/$rel" dst="$STATE/$dstrel"
    if ! cmp -s "$src" "$dst"; then
        mkdir -p "$(dirname "$dst")"
        install -m "$mode" "$src" "$dst"
        say "updated $dstrel (mode $mode)"
        openclaw_changed=1
    elif [[ "$(stat -c %a "$dst" 2>/dev/null)" != "$mode" ]]; then
        # Content already matches, so this is a permission-only repair. It needs no
        # restart: the agent re-reads nothing, and the file it reads has not changed.
        chmod "$mode" "$dst"
        say "corrected mode on $dstrel to $mode"
    fi
done
[[ $openclaw_changed -eq 0 ]] && say "openclaw config unchanged"

# Skills go into the agent's state dir, which is where OpenClaw looks for skills shared
# by every agent on the machine (~/.openclaw/skills inside the container -- the same
# path in 2026.3.2 and in current releases, and it is this bind mount).
#
# --delete is scoped to each skill's OWN directory, never to $STATE/skills as a whole:
# a removed package file should disappear, but a skill the agent installed for itself
# is not deploy's to delete. That keeps apply.sh's rule intact -- copy config in, never
# delete from the state dir.
if [[ -d "$HERE/skills" ]]; then
    for skill_dir in "$HERE/skills"/*/; do
        [[ -d "$skill_dir" ]] || continue
        name=$(basename "$skill_dir")
        mkdir -p "$STATE/skills/$name"
        # Content, not timestamps. `rsync -ai` reports a difference when only the mtime
        # differs, and two git checkouts of identical content on two machines have
        # different mtimes -- so deploying from a different origin than last time
        # reported "skill differs" and restarted the agent for nothing, dropping Slack
        # each time. diff -rq compares bytes and says nothing about when they arrived.
        if ! diff -rq "$skill_dir" "$STATE/skills/$name" >/dev/null 2>&1; then
            rsync -a --delete "$skill_dir" "$STATE/skills/$name/"
            say "installed skill $name"
            openclaw_changed=1
        fi
    done
fi

docker compose up -d

# `up -d` won't restart openclaw when only a file inside the bind mount changed,
# and OpenClaw caches its config at startup — so a config-only change would
# otherwise deploy cleanly and take effect never.
if [[ $openclaw_changed -eq 1 ]]; then
    docker compose restart openclaw
    say "openclaw restarted to pick up new config"
fi

# Last, so a pending manual step is the final thing on screen rather than scrolled past.
openclaw_state_checks

# Alloy's config is a bind mount, which `up -d` does not watch. Restart it when the shared
# file's content changed since the last apply (bastion does the same for its whole stack).
if [[ -e "$HERE/scripts/alloy/config.alloy" ]] && docker inspect alloy >/dev/null 2>&1; then
    applied="/home/robby/.alloy-config.sha256"
    now=$(sha256sum "$HERE/scripts/alloy/config.alloy" | cut -c1-64)
    if [[ "$(cat "$applied" 2>/dev/null)" != "$now" ]]; then
        docker compose restart alloy >/dev/null && say "alloy restarted for new config"
        echo "$now" > "$applied"
    fi
fi

docker compose ps --format '    {{.Name}}\t{{.Image}}\t{{.Status}}'
