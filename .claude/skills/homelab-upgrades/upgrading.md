# Upgrading, step by step

Read this when an upgrade is actually going to happen. `SKILL.md` has the tiers, the rules and how
to tell what is behind.

## Upgrading a Watch package

These move slowly and the hop is small, so this is ordinary work, not a project.

1. Read `packages/<name>.md` — the hazard, what to back up, and how to verify.
2. Check the backup the runbook names is recent.
3. Bump the tag in the compose file the package file points at. Change the one `image:` line.
4. `./deploy.sh --check <host>` first, then `./deploy.sh <host>`.
5. Work through `Verify after`. If it fails, follow `Rollback` — do not debug forward on a host
   running DNS or the vault.

## Planning a Drift upgrade

A Drift package is usually many releases and months behind, so this needs a written plan before
anything is edited. Produce, in this order:

1. **How far behind, concretely** — releases and elapsed time, from version-shaped tags only.
2. **Intermediate hops.** Do not cross many months in one bite. Pick a mid-point release, land it,
   verify, continue. The plan names the hops.
3. **The contracts to check first.** This is the part people skip. Other things in this repo call
   these packages and parse their output; a major jump can change a CLI flag, an output shape or a
   config schema, and the failure shows up somewhere else entirely. Each package file lists its
   own under `Contracts`.
4. **Backups, verified by age** — not assumed to exist.
5. **Verification and rollback**, from the runbook.

Release notes are the input to this plan. They are large: some of these projects publish more than
100 KB per release, which does not fit in the local model's context on forge. Read them
newest-first, one release at a time, stopping once the breaking changes and any security notes are
in hand — and prefer the project's own upgrade or migration guide over the changelog once the
backlog is more than a few releases deep. When the notes genuinely do not fit, say so and plan
from the migration guide rather than summarising a fraction of them silently.
