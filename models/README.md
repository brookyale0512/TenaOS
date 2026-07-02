# models/

Host-mounted directory for the `llama.cpp` GGUF weights. Gitignored — the
files are large binary blobs and live outside source control.

## Required files

| File | Size | Source |
| --- | --- | --- |
| `tenaos-gemma-4-E4B-it-lora-F16.gguf` | ~15 GB  | [`beza4588/TenaOS`](https://huggingface.co/beza4588/TenaOS) |
| `mmproj-gemma-4-E4B-it-bf16.gguf`     | ~946 MB | [`beza4588/TenaOS`](https://huggingface.co/beza4588/TenaOS) |

`tenaos-gemma-4-E4B-it-lora-F16.gguf` is **Gemma 4 E4B with the TenaOS
task-tagged LoRA adapter merged in** (full precision, F16) — not the
plain base model. TenaOS always serves this merged build so that the
`[form]`, `[report]`, `[scribe]`, `[scribe-am]`, `[cds]`, and `[edu]`
task-tag routing described in the README is actually active at
runtime. The mmproj projector is unaffected by the LoRA merge (only
attention/MLP projections were adapted) and stays the base file — this
matches the "Merged LoRA model" example on the model card exactly.

## Easiest: use the bootstrap script

From the repo root:

```bash
bash scripts/fetch-models.sh
```

That fetches both GGUFs into `./tenaos-bootstrap/models/`. Set
`TENAOS_MODELS_PATH=$(pwd)/tenaos-bootstrap/models` in `.env` so
Docker Compose bind-mounts that directory at `/models`.

## Manual download

```bash
hf download beza4588/TenaOS \
  tenaos-gemma-4-E4B-it-lora-F16.gguf mmproj-gemma-4-E4B-it-bf16.gguf \
  --local-dir ./models
```

## If you want the plain base model instead (not recommended)

The same repo also hosts the un-merged base weights
(`gemma-4-E4B-it-BF16.gguf`) for comparison/ablation purposes. Running
TenaOS against the base file works, but the adapter's task-tag
behavior — the actual clinical-workflow tuning this project is built
around — will not be active. Building the merged GGUF yourself from
scratch requires merging `adapter/adapter_model.safetensors` into
`google/gemma-4-E4B-it` with PEFT before GGUF conversion; downloading
the pre-merged file above is far simpler and is what every documented
TenaOS deployment path expects.

## Verification

```bash
ls -lh models/
file models/tenaos-gemma-4-E4B-it-lora-F16.gguf  # must start with "GGUF"
```

`TenaOS-LLM` refuses to start if either file is missing — the container
entrypoint checks for `/models/tenaos-gemma-4-E4B-it-lora-F16.gguf` and
aborts loudly if it isn't there.

## What this directory does NOT contain

| Artifact | Why removed |
| --- | --- |
| `gemma-4-E4B-it-Q8_0.gguf`   | Quantized comparison baseline — public release standardizes on the merged F16 build |
| `gemma-4-E4B-it.litertlm`    | LiteRT-LM runtime removed |
