# TenaOS — single all-in-one image.
#
# One container, all services. Designed for LMIC operators who just want
# a single `docker run` line and a working clinical workspace.
#
# Internal layout (managed by supervisord):
#   nginx           :80    serves frontend + reverse-proxies /openmrs and /agent-api
#   tomcat-openmrs  :8080  OpenMRS Reference Application 3
#   mariadb         :3306  OpenMRS database
#   qdrant          :6333  vector store (who_msf_guidelines + ciel_concepts)
#   llama-server    :8001  llama.cpp serving Gemma 4 E4B BF16 GGUF
#   tena-agent      :8095  Python agent service
#   kb-guidelines   :4276  WHO/MSF retrieval daemon (loads EmbedGemma in-process)
#   kb-ciel         :4277  CIEL semantic-search daemon (loads EmbedGemma in-process)
#
# Bind-mount the big artifacts at runtime; never bake them into the image:
#   ./models                                    -> /models                                 (~16 GB GGUF weights)
#   ./embedgemma-300m                           -> /opt/tenaos/embedgemma-300m            (~1.2 GB)
#   ./ciel_search.sqlite3                       -> /opt/tenaos/ciel/ciel_search.sqlite3   (~1.7 GB)
#
# Persist runtime state via named volumes:
#   tenaos-openmrs-data  -> /opt/openmrs/data
#   tenaos-mariadb-data  -> /var/lib/mysql
#   tenaos-qdrant-data   -> /qdrant/storage
#   tenaos-agent-runtime -> /opt/tenaos/runtime

ARG TENAOS_OMRS_SOURCE_IMAGE=openmrs/openmrs-reference-application-3-backend:demo@sha256:88099af8a3461cf4f3b4978e45e56f36974c50913ed1cf9f13f42c60970f1490

# ── Stage 1: pull OpenMRS distribution from the upstream image ────────────
FROM ${TENAOS_OMRS_SOURCE_IMAGE} AS omrs-src

# ── Stage 2: build llama.cpp from a pinned upstream commit SHA ───────────
# We pin a commit SHA, not a tag, because ggerganov/llama.cpp publishes
# rolling tags that are garbage-collected upstream (b6005 — the previous
# pin — vanished after ~4 weeks). SHAs are immutable.
#
# To bump: pick a tag at https://github.com/ggerganov/llama.cpp/releases,
# resolve it to a commit SHA, and update LLAMA_CPP_REF below.
FROM nvidia/cuda:12.6.1-devel-ubuntu24.04 AS llama-build
ARG LLAMA_CPP_REF=b0df4c0cfd2cda10738056771714a5290dc95454
ARG CMAKE_CUDA_ARCHITECTURES=80
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git ca-certificates curl libcurl4-openssl-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src/llama.cpp
RUN git init -q . \
    && git remote add origin https://github.com/ggerganov/llama.cpp.git \
    && git fetch --depth 1 origin "${LLAMA_CPP_REF}" \
    && git checkout -q FETCH_HEAD \
    && cmake -B build -DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=${CMAKE_CUDA_ARCHITECTURES} -DLLAMA_CURL=ON \
    && cmake --build build --config Release --target llama-server -j

# ── Stage 3: build the frontend SPA ───────────────────────────────────────
FROM node:24-bookworm-slim AS frontend-build
WORKDIR /workspace
COPY TenaOS-Frontend/package.json TenaOS-Frontend/package-lock.json TenaOS-Frontend/
RUN cd TenaOS-Frontend && npm ci
COPY TenaOS-Frontend/ TenaOS-Frontend/
COPY TenaOS-Backend/metadata/required-openmrs-metadata.json \
     TenaOS-Backend/metadata/required-openmrs-metadata.json
WORKDIR /workspace/TenaOS-Frontend
RUN npm run build

# ── Stage 4: the runtime image ────────────────────────────────────────────
# ubuntu24.04 base — MariaDB 10.11 (matches existing data), glibc 2.39 (qdrant).
FROM nvidia/cuda:12.6.1-runtime-ubuntu24.04

ARG TENAOS_MYSQL_CONNECTOR_J_URL=https://repo1.maven.org/maven2/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar
ARG TENAOS_MYSQL_CONNECTOR_J_SHA256=e2a3b2fc726a1ac64e998585db86b30fa8bf3f706195b78bb77c5f99bf877bd9
ARG QDRANT_VERSION=v1.15.4
# Pinned upstream SHA-256 of the x86_64-unknown-linux-gnu tarball published at
#   https://github.com/qdrant/qdrant/releases/download/${QDRANT_VERSION}/qdrant-x86_64-unknown-linux-gnu.tar.gz
# Update both ARGs together when bumping QDRANT_VERSION.
ARG QDRANT_SHA256=8d0f3af71b4606581c6d4d943c0763aa7f368a7ee801b2f01e16ab2376e8f363

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=UTC

# Base OS packages: Java 21, MariaDB 10.11, supervisord, nginx, python3, curl, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg tzdata unzip \
        openjdk-21-jdk-headless \
        mariadb-server \
        supervisor \
        nginx \
        python3 python3-venv python3-pip \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64

# ── OpenMRS distribution + Tomcat ─────────────────────────────────────────
COPY --from=omrs-src /usr/local/tomcat /opt/tomcat-openmrs
RUN rm -rf /opt/tomcat-openmrs/webapps/*

RUN mkdir -p /opt/openmrs/distribution /opt/openmrs/data
COPY --from=omrs-src /openmrs/distribution/   /opt/openmrs/distribution/
COPY --from=omrs-src /openmrs/startup-init.sh /opt/openmrs/startup-init.sh
COPY --from=omrs-src /openmrs/startup.sh      /opt/openmrs/startup.sh
COPY --from=omrs-src /openmrs/wait-for-it.sh  /opt/openmrs/wait-for-it.sh

# Reuse the existing OpenMRS patch scripts and address hierarchy seed.
COPY TenaOS-Backend/build/patch-openmrs-log4j.py /tmp/patch-openmrs-log4j.py
COPY TenaOS-Backend/build/patch-openmrs-webservices.py /tmp/patch-openmrs-webservices.py
RUN if [ -f /opt/openmrs/distribution/openmrs_config/addresshierarchy/referenceapplication-demo/addressConfiguration.xml ]; then \
      cp /opt/openmrs/distribution/openmrs_config/addresshierarchy/referenceapplication-demo/addressConfiguration.xml \
         /opt/openmrs/distribution/openmrs_config/addresshierarchy/addressConfiguration.xml; \
    fi && \
    if [ -f /opt/openmrs/distribution/openmrs_config/addresshierarchy/referenceapplication-demo/addresshierarchy.csv ]; then \
      cp /opt/openmrs/distribution/openmrs_config/addresshierarchy/referenceapplication-demo/addresshierarchy.csv \
         /opt/openmrs/distribution/openmrs_config/addresshierarchy/addresshierarchy.csv; \
    fi && \
    python3 /tmp/patch-openmrs-log4j.py && \
    python3 /tmp/patch-openmrs-webservices.py && \
    rm -f /tmp/patch-openmrs-log4j.py /tmp/patch-openmrs-webservices.py

RUN chmod +x /opt/openmrs/startup-init.sh /opt/openmrs/startup.sh /opt/openmrs/wait-for-it.sh && \
    ln -sfn /opt/tomcat-openmrs /usr/local/tomcat && \
    ln -sfn /opt/openmrs /openmrs

RUN curl -fsSL "$TENAOS_MYSQL_CONNECTOR_J_URL" -o /tmp/mysql-connector-j.jar && \
    echo "${TENAOS_MYSQL_CONNECTOR_J_SHA256}  /tmp/mysql-connector-j.jar" | sha256sum -c - && \
    mv /tmp/mysql-connector-j.jar /opt/tomcat-openmrs/lib/mysql-connector-j.jar

COPY TenaOS-Backend/scripts/ /opt/tenaos/openmrs-scripts/
COPY TenaOS-Backend/configs/ /opt/tenaos/openmrs-configs/
RUN chmod +x /opt/tenaos/openmrs-scripts/*.sh /opt/tenaos/openmrs-scripts/lib/*.sh

# ── Qdrant ────────────────────────────────────────────────────────────────
RUN curl -fsSL "https://github.com/qdrant/qdrant/releases/download/${QDRANT_VERSION}/qdrant-x86_64-unknown-linux-gnu.tar.gz" \
        -o /tmp/qdrant.tar.gz && \
    echo "${QDRANT_SHA256}  /tmp/qdrant.tar.gz" | sha256sum -c - && \
    tar -xzf /tmp/qdrant.tar.gz -C /usr/local/bin/ && \
    rm /tmp/qdrant.tar.gz && \
    mkdir -p /qdrant/storage /qdrant/snapshots /qdrant/config

# Qdrant default config (storage path + listen).
RUN printf '%s\n' \
    'storage:' \
    '  storage_path: /qdrant/storage' \
    '  snapshots_path: /qdrant/snapshots' \
    'service:' \
    '  host: 127.0.0.1' \
    '  http_port: 6333' \
    '  grpc_port: 6334' \
    > /qdrant/config/config.yaml

# ── llama.cpp (compiled in stage 2 from pinned upstream tag) ──────────────
COPY --from=llama-build /src/llama.cpp/build/bin/llama-server /opt/tenaos/llm/llama-server
COPY --from=llama-build /src/llama.cpp/build/bin/lib*.so      /opt/tenaos/llm/

# ── Python application code + dependencies ───────────────────────────────
COPY TenaAgent/service/requirements.txt /tmp/tena-agent-requirements.txt
COPY TenaOS-KnowledgeBase/requirements.txt /tmp/kb-requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages --ignore-installed --upgrade pip setuptools wheel && \
    pip3 install --no-cache-dir --break-system-packages \
        -r /tmp/tena-agent-requirements.txt \
        -r /tmp/kb-requirements.txt \
    && rm /tmp/tena-agent-requirements.txt /tmp/kb-requirements.txt

# TenaAgent + KB + CIEL source.
COPY TenaAgent /opt/tenaos/TenaAgent
COPY TenaOS-KnowledgeBase /opt/tenaos/TenaOS-KnowledgeBase
COPY TenaOS-CIEL /opt/tenaos/TenaOS-CIEL

# Frontend dist + custom nginx config.
COPY --from=frontend-build /workspace/TenaOS-Frontend/dist/ /usr/share/nginx/html/
COPY docker/nginx.conf /etc/nginx/sites-available/tenaos.conf
RUN rm -f /etc/nginx/sites-enabled/default && \
    ln -sf /etc/nginx/sites-available/tenaos.conf /etc/nginx/sites-enabled/tenaos.conf

# Supervisord configuration + entrypoint.
COPY docker/supervisord.conf /etc/supervisor/supervisord.conf
COPY docker/entrypoint.sh /usr/local/bin/tenaos-entrypoint
COPY docker/start-llama.sh /usr/local/bin/tenaos-start-llama
COPY docker/start-tena-agent.sh /usr/local/bin/tenaos-start-tena-agent
COPY docker/start-kb.sh /usr/local/bin/tenaos-start-kb
COPY docker/restore-qdrant.sh /usr/local/bin/tenaos-restore-qdrant
RUN chmod +x /usr/local/bin/tenaos-entrypoint \
              /usr/local/bin/tenaos-start-llama \
              /usr/local/bin/tenaos-start-tena-agent \
              /usr/local/bin/tenaos-start-kb \
              /usr/local/bin/tenaos-restore-qdrant

# Users + directories.
RUN groupadd --system tenaos && \
    useradd --system --gid tenaos --create-home --home-dir /home/tenaos --shell /usr/sbin/nologin tenaos && \
    mkdir -p /run/mysqld /var/lib/mysql /var/log/supervisor /var/log/openmrs /var/log/tenaos \
             /opt/tenaos/runtime /opt/tenaos/ciel /opt/tenaos/embedgemma-300m \
             /opt/tenaos/data /opt/tenaos/data/emr-os/openmrs-managed-config \
             /var/lib/lucene_index /target && \
    chown mysql:mysql /run/mysqld /var/lib/mysql && \
    chown -R tenaos:tenaos /opt/openmrs /opt/tomcat-openmrs /var/log/openmrs /var/log/tenaos \
                          /opt/tenaos/runtime /opt/tenaos/TenaAgent \
                          /opt/tenaos/data /var/lib/lucene_index /target

# Environment defaults (overridable at `docker run` time).
ENV LD_LIBRARY_PATH=/opt/tenaos/llm:/usr/local/cuda/lib64 \
    PYTHONPATH=/opt/tenaos/TenaAgent/service:/opt/tenaos/TenaOS-KnowledgeBase:/opt/tenaos/TenaOS-CIEL \
    TENA_AGENT_ROOT=/opt/tenaos/TenaAgent \
    TENA_AGENT_RUNTIME_DIR=/opt/tenaos/runtime \
    TENAOS_LLM_URL=http://127.0.0.1:8001/v1 \
    TENAOS_LLM_MODEL=gemma-4 \
    TENAOS_LLM_API_KEY=EMPTY \
    TENAOS_KB_GUIDELINES_URL=http://127.0.0.1:4276 \
    TENAOS_KB_CIEL_URL=http://127.0.0.1:4277 \
    TENAOS_CIEL_ROOT=/opt/tenaos/TenaOS-CIEL \
    TENAOS_CIEL_SQLITE=/opt/tenaos/ciel/ciel_search.sqlite3 \
    EMBEDGEMMA_PATH=/opt/tenaos/embedgemma-300m \
    TENAOS_QDRANT_URL=http://127.0.0.1:6333 \
    # TenaAgent binds to loopback only. The in-container nginx (also on
    # localhost) is the single ingress; never expose :8095 to the host.
    TENA_AGENT_SERVICE_HOST=127.0.0.1 \
    TENA_AGENT_SERVICE_PORT=8095 \
    OPENMRS_REST_BASE_URL=http://127.0.0.1:8080/openmrs/ws/rest/v1 \
    OPENMRS_FHIR_BASE_URL=http://127.0.0.1:8080/openmrs/ws/fhir2/R4 \
    OPENMRS_PORT=8080 \
    OPENMRS_DB_NAME=openmrs \
    OPENMRS_DB_USER=openmrs \
    OPENMRS_KEYCLOAK_ENABLED=false \
    OPENMRS_PUBLIC_HOST=localhost

EXPOSE 80
VOLUME ["/opt/openmrs/data", "/var/lib/mysql", "/qdrant/storage", "/opt/tenaos/runtime"]

# The container is healthy only when BOTH conditions hold:
#   1. The agent + nginx + LLM are answering /agent-api/health.
#   2. The one-shot qdrant-restore program reported success (marker file).
# If qdrant-restore failed, the agent would silently return zero-evidence
# CDS results, so we surface the failure at the container boundary.
HEALTHCHECK --interval=30s --timeout=10s --start-period=300s --retries=10 \
    CMD test -f /opt/tenaos/runtime/qdrant-restore.ok \
     && test ! -f /opt/tenaos/runtime/qdrant-restore.failed \
     && curl -fsS http://127.0.0.1/agent-api/health \
     || exit 1

ENTRYPOINT ["/usr/local/bin/tenaos-entrypoint"]
