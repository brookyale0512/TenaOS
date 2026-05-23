# models/

Host-mounted directory for the `llama.cpp` GGUF weights. Gitignored — the
files are large binary blobs and live outside source control.

## Required files

| File | Size | Source |
| --- | --- | --- |
| `gemma-4-E4B-it-BF16.gguf`         | ~15 GB  | [`beza4588/TenaOS`](https://huggingface.co/beza4588/TenaOS) |
| `mmproj-gemma-4-E4B-it-bf16.gguf`  | ~946 MB | [`beza4588/TenaOS`](https://huggingface.co/beza4588/TenaOS) |

Both files are full-precision **BF16** — no quantization on the
production path.

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
