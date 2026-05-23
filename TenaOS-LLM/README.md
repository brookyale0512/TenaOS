# TenaOS-LLM

The inference runtime for TenaOS. A `llama.cpp` CUDA server hosting
[Gemma 4 E4B Instruct](https://huggingface.co/google/gemma-4-E4B-it) at
BF16 precision, behind the standard OpenAI `/v1/chat/completions` API.

Only `TenaAgent` talks to this service. The browser never does.

## Purpose

| Why llama.cpp | Why BF16 GGUF |
| --- | --- |
| Native multimodal projector for Gemma 4 audio input | Full precision; no quantization artifacts |
| Tiny operational footprint vs. vLLM/TGI | Single ~16 GB file, easy to ship |
| OpenAI-compatible HTTP surface | Matches TenaAgent's existing client |

## Build

The Dockerfile ships a **prebuilt** `llama.cpp` binary tree from
`third_party/llama.cpp/sm80/`. This is the exact binary that the live
demo has been running against in production, so the image needs no
compile step.

```bash
docker build -t tenaos-llm:latest -f TenaOS-LLM/Dockerfile .
```

The `sm80` build targets **NVIDIA Ampere** (A100, A40, RTX 30xx). For
other architectures drop the matching prebuild into
`third_party/llama.cpp/<arch>/` and update the COPY path in the
Dockerfile:

| GPU family | Compute | Directory name |
| --- | --- | --- |
| Ampere (A100, A40, RTX 30xx) | 8.0 / 8.6 | `sm80` |
| Ada (RTX 4090, L40)         | 8.9       | `sm89` |
| Hopper (H100, H200)         | 9.0       | `sm90` |

## Run

```bash
docker run --rm --gpus all \
  -v $(pwd)/models:/models:ro \
  -p 8000:8000 \
  tenaos-llm:latest \
    -m /models/gemma-4-E4B-it-BF16.gguf \
    --mmproj /models/mmproj-gemma-4-E4B-it-bf16.gguf \
    --host 0.0.0.0 --port 8000 \
    -ngl 99 --ctx-size 0 --jinja --alias gemma-4 --no-webui
```

## Test

```bash
curl -fsS http://localhost:8000/v1/models | jq .
curl -fsS -X POST http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gemma-4","messages":[{"role":"user","content":"hi"}]}'
```

## Environment

The container is configured entirely through command-line flags; see
[`docker-compose.yml`](../docker-compose.yml) for the canonical invocation.
