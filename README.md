# TenaOS

> Previously known as ClinicDx (MedGemma challenge). Renamed to TenaOS for the
> Gemma 4 build. *Tena* (ጤና) is the Amharic word for "health".

TenaOS is the AI-native clinical operating system. It pairs a custom React/Vite
frontend with the OpenMRS Reference Application 3 backend (Tomcat + MariaDB) in
a single container, plus a same-origin nginx proxy that fronts both in
production.

## Project layout

- `backend/` - OpenMRS image, supervisor config, health checks, import scripts.
- `frontend/` - React/Vite SPA.
- `docs/` - architecture, deployment, and operational notes.
- `scripts/` - repo-wide helpers (CI hygiene guard, etc.).
- `runtime-artifacts/` - **gitignored** local SQL dumps and pre-import backups.
  Kept out of the working tree on commit; never published.

## Local runtime

```bash
cd backend
cp .env.example .env
./scripts/start.sh
./scripts/verify-lite.sh
```

OpenMRS is served at `http://localhost:18080/openmrs` by default. The frontend
is served by `npm run dev` at `http://localhost:5173`.

### Authentication

The frontend uses native OpenMRS session auth: the user signs in at
`/login`, OpenMRS issues a `JSESSIONID` cookie, and every subsequent
request flows through nginx with `withCredentials`. There is no static
admin Basic-auth header anywhere in the production image. For dev work
against an OpenMRS that lacks a sign-in UI, set `OPENMRS_DEV_INJECT_BASIC=true`
in `frontend/.env` to opt back into the legacy proxy injection.

## Importing a database dump

Store local SQL dumps under `runtime-artifacts/openmrs/imports/`, then run:

```bash
cd backend
./scripts/import-openmrs-db.sh ../runtime-artifacts/openmrs/imports/<dump>.sql
```

The import script backs up the existing database into
`runtime-artifacts/openmrs/backups/`, replaces the `openmrs` database, restores
the image-bundled OpenMRS modules into the data volume, and restarts OpenMRS.

The `runtime-artifacts/` directory is gitignored. The `scripts/ci-guard.sh`
script (run in CI) fails the pipeline if any SQL dump or runtime artifact
ever lands in version control.

## Frontend

```bash
cd frontend
npm ci
npm run lint        # ESLint
npm run typecheck   # tsc --noEmit
npm test            # Vitest unit + RTL component
npm run test:coverage
npm run test:smoke  # legacy network probe against a live OpenMRS
npm run test:e2e    # Playwright E2E (requires a running stack)
npm run build
```

Frontend runtime assumptions are configured in `frontend/.env.example`. UUID
defaults are sourced from `backend/metadata/required-openmrs-metadata.json` so
the metadata contract and the SPA never drift; a Vitest assertion enforces this.

For per-deployment overrides without a rebuild, edit
`frontend/public/runtime-config.json` (rendered by nginx) -- the SPA fetches it
on boot and applies any non-null fields over the build-time defaults.

## Production deployment

See [docs/production-deployment.md](docs/production-deployment.md).
Highlights:

- Native OpenMRS session login with `JSESSIONID` cookies.
- nginx serves the SPA and reverse-proxies `/openmrs/` with strict CSP,
  `X-Frame-Options: DENY`, `X-Content-Type-Options`, `Referrer-Policy`, and
  `Permissions-Policy` headers.
- Healthchecks authenticate as a least-privilege OpenMRS user provisioned via
  `backend/metadata/healthcheck-user.sql`; admin credentials are never used in
  routine probes.
- The frontend image bakes zero credentials. Any operator running the image
  must supply an OpenMRS upstream URL; authentication happens through the user
  sign-in flow.

## Production status

TenaOS Phase 1 is feature-complete for the OpenMRS-only clinical
workflow:

- Patient registration end-to-end with IDGen auto-generation, identifier
  format validation, and per-field server error mapping.
- Visit-bound vitals, notes, and lab orders (orphan encounters are no longer
  possible from the UI).
- Native OpenMRS session login with `RequireAuth` route guarding.
- Concept search pickers replace raw UUID input for diagnoses, notes, and lab
  orders.
- Comprehensive Vitest + RTL coverage on the critical-path libraries (>90% on
  the audited surface) plus Playwright E2E specs for sign-in and registration.

See [PHASE1_VALIDATION.md](PHASE1_VALIDATION.md) for the current pass/fail
matrix per workflow.

## Security disclosure

If you find a vulnerability, please email security@tenaos.example (replace
with your operational address) before opening an issue. Do not commit real
patient data, secrets, or credentials at any point.
