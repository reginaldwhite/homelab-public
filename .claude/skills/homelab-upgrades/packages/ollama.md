# ollama (forge)

```json
{ "name": "ollama", "tier": "drift", "host": "forge", "container": "ollama",
  "compose": "forge/docker-compose.yaml",
  "image": "ollama/ollama",
  "upstream": { "kind": "github-release", "repo": "ollama/ollama" },
  "tag_from_release": "strip-v" }
```

## Hazard

Low. State is a model cache in the `ollama_data` volume and is fully reproducible by re-pulling —
this is the cheapest rollback of any package here. The risk is not data loss but **silently moving
the agent's inference behaviour**, which is why the tag is pinned rather than left on `:latest`.

The one real trap is environment tuning, not the upgrade itself.

## The tag appears TWICE

`forge/docker-compose.yaml` uses this image for **two** services: `ollama` and `model-loader`. They
must stay in step — the model-loader runs the ollama CLI against the server. **Bump both `image:`
lines**, or `apply.sh --validate` will pass and you will be running two versions against one
volume.

## Contracts

- `OLLAMA_CONTEXT_LENGTH=16384` is load-bearing. ollama's own default lands at 4096 here, which is
  below OpenClaw's ~16000 minimum and stops the agent from running at all. 16384 is also the largest
  value that keeps all three configured models at 100% GPU on a 16 GB card — `gpt-oss:20b` (MoE)
  also fits 32768, but the dense models spill to CPU there. If a release changes how this variable
  is read or defaulted, the agent stops working rather than getting slower.
- `OLLAMA_KEEP_ALIVE=-1` (never unload) and `OLLAMA_MAX_LOADED_MODELS=1` (evict rather than
  co-locate) are deliberate for the 16 GB card. Check a release has not renamed either.
- The healthcheck is `ollama list`. The image ships no curl, wget or python, so everything goes
  through the CLI — a CLI change can break the healthcheck and therefore `depends_on` for both
  openclaw and open-webui.
- `forge/scripts/gpu-guard.sh` exists because GDM unloads the nvidia kernel modules a few minutes
  after boot, leaving the container unable to reach the driver and silently moving inference to the
  CPU. Read its header before blaming an upgrade for slow inference.

## Before upgrading

- Note which models are pulled. `model-loader` re-pulls
  `qwen2.5-coder:14b`, `gpt-oss:20b` and the Apriel GGUF on start, so a wiped volume costs
  bandwidth and time rather than data.
- Check the release notes for changes to GPU scheduling or the env vars above.

## Verify after

```bash
ssh robby@192.168.0.12 "docker exec ollama ollama list"
ssh robby@192.168.0.12 "docker logs --tail 40 ollama | grep -i -E 'gpu|cuda|error'"
```

Confirm inference is still **on the GPU** and not silently on the CPU — that is the failure this
setup has actually had:

```bash
ssh robby@192.168.0.12 "nvidia-smi --query-gpu=memory.used --format=csv"
ssh robby@192.168.0.12 "docker exec ollama ollama ps"
```

`ollama ps` should show the model resident with a GPU percentage at 100%. Then confirm the agent
answers in Slack, since a working ollama with a broken context length looks fine here.

## Rollback

Restore both `image:` lines to the previous tag and redeploy. The volume is compatible across these
versions in practice; if it is not, deleting `ollama_data` and letting `model-loader` re-pull is a
safe last resort.
