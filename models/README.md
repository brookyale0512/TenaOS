# models/

Host-mounted directory for the `llama.cpp` GGUF weights. Gitignored — the
files are large binary blobs and live outside source control.

> **Temporary notice:** TenaOS normally serves **Gemma 4 E4B with the
> TenaOS task-tagged LoRA adapter merged in**
> (`tenaos-gemma-4-E4B-it-lora-F16.gguf`) so that the `[form]`, `[report]`,
> `[scribe]`, `[scribe-am]`, `[cds]`, and `[edu]` task-tag routing described
> in the top-level README is active at runtime. The published adapter is
> currently being retrained to fix a data/production parity gap, so this
> directory (and `scripts/fetch-models.sh`) is temporarily pinned to the
> **plain base model** below instead. This notice — and the referenced
> scripts — will be reverted once the new adapter is validated and
> republished.

## Required files (temporary: base model)

| File | Size | Source |
| --- | --- | --- |
| `gemma-4-E4B-it-BF16.gguf`        | ~15 GB  | [`beza4588/TenaOS`](https://huggingface.co/beza4588/TenaOS) |
| `mmproj-gemma-4-E4B-it-bf16.gguf` | ~946 MB | [`beza4588/TenaOS`](https://huggingface.co/beza4588/TenaOS) |

Both files are full-precision **BF16** — no quantization on this
temporary path. Once the retrained adapter ships, this reverts to the
merged `tenaos-gemma-4-E4B-it-lora-F16.gguf` build.

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
  gemma-4-E4B-it-BF16.gguf mmproj-gemma-4-E4B-it-bf16.gguf \
  --local-dir ./models
```

## Manual conversion (if you don't want to download)

```bash
git clone https://github.com/ggerganov/llama.cpp.git && cd llama.cpp
pip install -r requirements/requirements-convert_hf_to_gguf.txt

hf download google/gemma-4-E4B-it --local-dir /tmp/gemma-4-E4B-it

python convert_hf_to_gguf.py /tmp/gemma-4-E4B-it \
  --outfile /var/www/TenaOS/models/gemma-4-E4B-it-BF16.gguf \
  --outtype bf16
```

The `mmproj` projector is published alongside Gemma 4 in the official
GGUF release; download or convert it the same way.

## Verification

```bash
ls -lh models/
file models/gemma-4-E4B-it-BF16.gguf  # must start with "GGUF"
```

`TenaOS-LLM` refuses to start if either file is missing — the container
entrypoint checks for `/models/gemma-4-E4B-it-BF16.gguf` and aborts
loudly if it isn't there.

## What this directory does NOT contain

| Artifact | Why removed |
| --- | --- |
| `gemma-4-E4B-it-Q8_0.gguf`   | Quantized comparison baseline — public release standardizes on BF16 |
| `gemma-4-E4B-it.litertlm`    | LiteRT-LM runtime removed |
