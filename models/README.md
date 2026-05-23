# models/

Host-mounted model weights for `TenaOS-LLM`. This directory is
gitignored — the files are large binary blobs and live outside source
control.

## Required files

| File | Size | Source |
| --- | --- | --- |
| `gemma-4-E4B-it-BF16.gguf` | ~16 GB | Convert from [`google/gemma-4-E4B-it`](https://huggingface.co/google/gemma-4-E4B-it) safetensors |
| `mmproj-gemma-4-E4B-it-bf16.gguf` | ~0.5 GB | Audio multimodal projector for Gemma 4 |

## How to build them

```bash
# 1. Clone llama.cpp (any recent release)
git clone https://github.com/ggerganov/llama.cpp.git
cd llama.cpp
pip install -r requirements/requirements-convert_hf_to_gguf.txt

# 2. Download the source model
huggingface-cli download google/gemma-4-E4B-it --local-dir /tmp/gemma-4-E4B-it

# 3. Convert to BF16 GGUF
python convert_hf_to_gguf.py /tmp/gemma-4-E4B-it \
  --outfile /var/www/TenaOS/models/gemma-4-E4B-it-BF16.gguf \
  --outtype bf16

# 4. The mmproj projector ships with the upstream GGUF release; download
#    it directly from the HuggingFace mirror or from the Gemma release.
```

## Verification

After downloading or building, sanity-check both files:

```bash
ls -lh models/
file models/gemma-4-E4B-it-BF16.gguf
```

Both files must start with the GGUF magic bytes (`GGUF`). `TenaOS-LLM`
will refuse to start if either is missing.

## What this directory does NOT contain

| Artifact | Why removed in 0.1.0 |
| --- | --- |
| `gemma-4-E4B-it-Q8_0.gguf`   | Quantized comparison baseline — the public release standardizes on BF16 |
| `gemma-4-E4B-it.litertlm`    | LiteRT-LM runtime removed in 0.1.0 |
