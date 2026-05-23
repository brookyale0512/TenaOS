# TenaOS-Backend

OpenMRS Reference Application 3 + MariaDB packaged as a single Docker
image. In the production all-in-one image, nginx is the only public
ingress and reverse-proxies both OpenMRS and TenaAgent.

## Purpose

- Hosts the patient record (REST and FHIR R4).
- Ships the OpenMRS metadata contract that TenaAgent and the SPA depend
  on (`metadata/required-openmrs-metadata.json`).
- Provides a least-privilege healthcheck user separate from `admin`.

## Build

```bash
docker build -t tenaos-backend:latest -f TenaOS-Backend/Dockerfile TenaOS-Backend
```

## Run (standalone local development only)

The `docker-compose.yml` in this directory is a local/backend
development stack for running OpenMRS with TenaAgent while you point at
an external `TenaOS-LLM` endpoint. It is not a production deployment
topology.

```bash
cd TenaOS-Backend
cp .env.example .env
docker compose up -d
```

OpenMRS lives at `http://localhost:18080/openmrs/`.

TenaAgent is available locally at `http://127.0.0.1:8095/` by default.
The compose file binds this port to loopback only:

```yaml
ports:
  - "127.0.0.1:${TENA_AGENT_SERVICE_PORT:-8095}:8095"
```

Do not expose TenaAgent directly to untrusted networks until it has its
own API authentication layer. If you need to test from the same machine,
use the loopback URL above. Inter-container communication is unaffected:
OpenMRS and TenaAgent continue to reach each other over the Docker
network.

For production-style deployments, use the all-in-one image from the
repository root. In that image, TenaAgent binds to container loopback and
nginx is the only ingress via `/agent-api`; host port `8095` should not
be published.

## Test

```bash
./scripts/verify-lite.sh
```

## Environment

| Variable | Purpose |
| --- | --- |
| `OPENMRS_DB_PASSWORD`        | MariaDB password (required) |
| `OPENMRS_ADMIN_PASSWORD`     | Initial OpenMRS admin password (required) |
| `OPENMRS_HEALTHCHECK_USERNAME` / `_PASSWORD` | Least-privilege healthcheck creds |
| `OPENMRS_JAVA_MEMORY_OPTS`   | JVM heap (default `-Xmx4g`) |
| `TENAOS_PUBLIC_HOST`         | Hostname used to compute `OPENMRS_PUBLIC_URL` |

## Layout

```
TenaOS-Backend/
├── Dockerfile
├── docker-compose.yml         Standalone OpenMRS + TenaAgent dev stack
├── .env.example
├── lmic_emr_os/               Container init scripts + runtime apply
├── metadata/                  required-openmrs-metadata.json + seed SQL
├── scripts/                   start.sh, verify-lite.sh, healthcheck helpers
└── docs/                      Operator contracts and reference docs
```
