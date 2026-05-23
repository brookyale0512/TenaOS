# TenaOS-Frontend

React + Vite single-page application. The clinical workspace lives here:
patient lists, encounters, the natural-language form builder, the report
builder, the SOAP scribe, and the agent insights surface.

The SPA never talks to the model directly — every AI call is proxied
through `TenaAgent` over `/agent-api`.

## Purpose

- Render the clinical workspace.
- Proxy `/openmrs/*` → `TenaOS-Backend` and `/agent-api/*` → `TenaAgent`.
- Forward the OpenMRS `JSESSIONID` cookie so authentication is handled
  entirely by the OpenMRS login page.
- Enforce the UUID contract from `TenaOS-Backend/metadata/required-openmrs-metadata.json`.

## Build

```bash
cd TenaOS-Frontend
npm ci
npm run build
```

Production image is built from the repo root:

```bash
docker build -t tenaos-frontend:latest -f TenaOS-Frontend/Dockerfile .
```

## Run (dev)

```bash
cd TenaOS-Frontend
npm run dev
# http://localhost:5173 with /openmrs and /agent-api proxied to localhost
```

## Test

```bash
npm run lint
npm run typecheck
npm test
npm run build
```

## Environment

The container reads only two runtime env vars (both for the nginx
template):

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENMRS_UPSTREAM_URL`    | `http://tenaos-backend:8080/openmrs/` | Where to proxy `/openmrs/` |
| `TENA_AGENT_UPSTREAM_URL` | `http://tenaagent:8095`               | Where to proxy `/agent-api/` |

OpenMRS credentials are **never** baked into the image; the SPA's
`/login` page sets the session cookie that nginx forwards.
