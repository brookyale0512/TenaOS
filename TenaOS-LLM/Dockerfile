# TenaOS-LLM — llama.cpp CUDA server for Gemma 4 E4B (BF16 GGUF).
#
# Ships a prebuilt llama.cpp binary tree under `third_party/llama.cpp/sm80/`.
# The `sm80` directory targets NVIDIA Ampere GPUs (A100, A40, RTX 30xx).
# To target a different arch (Ada `sm89`, Hopper `sm90`), drop the matching
# build under `third_party/llama.cpp/<arch>/` and update the COPY path below.
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

WORKDIR /opt/tenaos/llm

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY third_party/llama.cpp/sm80/bin/ /opt/tenaos/llm/

ENV LD_LIBRARY_PATH=/opt/tenaos/llm:${LD_LIBRARY_PATH}
ENV LLAMA_ARG_HOST=0.0.0.0
ENV LLAMA_ARG_PORT=8000

EXPOSE 8000

ENTRYPOINT ["/opt/tenaos/llm/llama-server"]
