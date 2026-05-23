# TenaOS-Backend

OpenMRS Reference Application 3 + MariaDB packaged as a single Docker
image, supervised so TenaAgent and the SPA can talk to one endpoint.

## Purpose

- Hosts the patient record (REST and FHIR R4).
- Ships the OpenMRS metadata contract that TenaAgent and the SPA depend
  on (`metadata/required-openmrs-metadata.json`).
- Provides a least-privilege healthcheck user separate from `admin`.

## Build

```bash
docker build -t tenaos-backend:latest -f TenaOS-Backend/Dockerfile TenaOS-Backend
```

## Run (standalone, for backend-only iteration)

```bash
cd TenaOS-Backend
cp .env.example .env
docker compose up -d
```

OpenMRS lives at `http://localhost:18080/openmrs/`.

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
| `OPENMRS_JAVA_MEMORY_OPTS`   | JVM heap (default `-Xmx1g`) |
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
