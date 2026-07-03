# TenaOS-LLM

The inference runtime inside the TenaOS container. A `llama.cpp` CUDA
server hosting
[Gemma 4 E4B](https://huggingface.co/beza4588/TenaOS)
at BF16/F16 precision, exposed on container localhost over the standard
OpenAI `/v1/chat/completions` API.

TenaOS normally serves the merged LoRA build, not plain base
[Gemma 4 E4B Instruct](https://huggingface.co/google/gemma-4-E4B-it) —
the adapter is what routes the `[form]`, `[report]`, `[scribe]`,
`[scribe-am]`, `[cds]`, and `[edu]` task tags described in the top-level
README.

> **Temporary notice:** the published adapter is currently being
> retrained to fix a data/production parity gap. Until it's validated
> and republished, this build serves the plain base BF16 model instead
> (see `docker/start-llama.sh` and the top-level README notice) — task
> tag routing is inactive until the revert.

Only `TenaAgent` talks to this service. The browser never does.

## Why these choices

| Why llama.cpp | Why the (normally merged) F16 GGUF |
| --- | --- |
| Native multimodal projector for Gemma 4 audio input | Full precision; no quantization artifacts |
| Small single-process serving stack | Single ~15 GB file, easy to bind-mount, no separate adapter-loading step |
| OpenAI-compatible HTTP surface | Matches TenaAgent's existing client |

## How it ships

`TenaOS-LLM` does **not** have its own Dockerfile. `llama.cpp` is
built from a pinned upstream tag inside the top-level
[`Dockerfile`](../Dockerfile) (multi-stage stage `llama-build`) and
copied into the final image at `/opt/tenaos/llm/`. To change the
upstream version, bump `LLAMA_CPP_TAG` and `CMAKE_CUDA_ARCHITECTURES`
in the top-level Dockerfile.

## Runtime layout

| Path inside container | Purpose |
| --- | --- |
| `/opt/tenaos/llm/llama-server` | The binary |
| `/opt/tenaos/llm/lib*.so`      | CUDA runtime libraries (LD_LIBRARY_PATH) |
| `/models/*.gguf`               | Bind-mounted host weights |
| `127.0.0.1:8001`               | Listen address (loopback only) |

## Environment

The supervisord program reads the runtime config from environment
variables (set in [`docker/start-llama.sh`](../docker/start-llama.sh)):

| Variable | Default | Purpose |
| --- | --- | --- |
| `TENAOS_LLM_GGUF`    | `/models/gemma-4-E4B-it-BF16.gguf` (temporary — normally `/models/tenaos-gemma-4-E4B-it-lora-F16.gguf`, merged base + LoRA) | Generation model |
| `TENAOS_LLM_MMPROJ`  | `/models/mmproj-gemma-4-E4B-it-bf16.gguf`     | Audio projector (base, unaffected by the LoRA merge) |
| `TENAOS_LLM_MODEL`   | `gemma-4`                                     | Alias served by the API |
| `TENAOS_LLM_CTX_SIZE`| `0`                                           | 0 = the model's native ctx |
