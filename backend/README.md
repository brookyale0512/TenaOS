# TenaOS Backend

OpenMRS Reference Application 3 plus MariaDB packaged into a single Docker
image, supervised so the SPA's Vite proxy or production nginx can talk to a
single endpoint.

## Quickstart

```bash
cp .env.example .env
docker compose up -d --build openmrs
./scripts/verify-lite.sh
```

`verify-lite.sh` polls the metadata primitives the frontend relies on:
locations, patient identifier types, visit types, and forms. Add `--write`
for an end-to-end registration smoke that creates and purges a synthetic
patient.

## Healthcheck

The container healthcheck runs `verify-lite.sh --internal --healthcheck`. In
production set `OPENMRS_HEALTHCHECK_USERNAME` / `OPENMRS_HEALTHCHECK_PASSWORD`
in the deployment environment so the probe authenticates as the dedicated
healthcheck user provisioned via [metadata/healthcheck-user.sql](metadata/healthcheck-user.sql)
rather than the OpenMRS admin.

## Importing a database dump

```bash
./scripts/import-openmrs-db.sh ../runtime-artifacts/openmrs/imports/<dump>.sql
```

The dumps live under `runtime-artifacts/openmrs/` (gitignored). The repo's
[scripts/ci-guard.sh](../scripts/ci-guard.sh) ensures none ever land in git.

## Structure

```
backend/
  Dockerfile            OpenMRS + supervisor image
  docker-compose.yml    OpenMRS + frontend services
  build/                Image-build helpers (log4j and webservices patches)
  configs/              supervisor config
  scripts/              start.sh, stop.sh, status.sh, run-openmrs.sh,
                         verify-lite.sh, init-databases.sh, import-openmrs-db.sh
  metadata/             required-openmrs-metadata.json (UUID contract) +
                         healthcheck-user.sql (least-privilege user provisioning)
  lmic_emr_os/          Python orchestration package (used by the agentic
                         control plane in the broader TenaOS project)
```

## Auth posture

OpenMRS REST is reached through the frontend's same-origin proxy. The lite
runtime turns off Keycloak / OAuth modules in
[scripts/run-openmrs.sh](scripts/run-openmrs.sh) so the OpenMRS session
endpoint is the canonical authenticator. No static admin credentials are
baked into either the backend or frontend images; rotate
`OPENMRS_ADMIN_PASSWORD` and `OPENMRS_HEALTHCHECK_PASSWORD` before any
external exposure.
