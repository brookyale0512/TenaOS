# TenaOS Frontend

> Previously named ClinicDx Lite (MedGemma challenge). Renamed to TenaOS for the Gemma 4 build.

React + Vite SPA that talks to OpenMRS through the same-origin `/openmrs`
reverse proxy.

## Quickstart

```bash
npm ci
npm run dev   # http://localhost:5173

# Static checks
npm run lint
npm run typecheck

# Tests
npm test                # Vitest unit + RTL component
npm run test:coverage
npm run test:e2e        # Playwright (requires a running OpenMRS + dev server)
npm run test:smoke      # legacy network probe against a live OpenMRS

npm run build           # TypeScript + Vite production build
npm run preview         # preview the built bundle
```

## Configuration layers

The SPA reads configuration in three layers, latest-wins:

1. Build-time defaults from `frontend/.env` (`VITE_*`).
2. Backend metadata contract (`backend/metadata/required-openmrs-metadata.json`)
   imported into [`src/lib/openmrs/runtimeConfig.ts`](src/lib/openmrs/runtimeConfig.ts).
   A Vitest test enforces parity with the contract.
3. Per-deployment runtime overrides served from
   [`public/runtime-config.json`](public/runtime-config.json). The SPA fetches
   this on boot via [`loadRuntimeConfig()`](src/lib/openmrs/runtimeConfig.ts).

## Authentication

Sign-in lives at `/login` ([`src/features/auth/LoginPage.tsx`](src/features/auth/LoginPage.tsx)).
On success, OpenMRS sets a `JSESSIONID` cookie and the SPA's axios client
automatically includes it via `withCredentials: true`.
[`RequireAuth`](src/features/auth/RequireAuth.tsx) gates the AppShell and a
401 from any request invalidates the cached session immediately.

For dev work against an OpenMRS without a sign-in UI, set
`OPENMRS_DEV_INJECT_BASIC=true` in `.env` to opt back into the legacy proxy
admin Basic-auth injection. Production builds never include this header.

## File layout

```
src/
  features/         feature folders (auth, patients, visits, clinical, ...)
  components/       shared UI primitives (Card, Button, Workspace, ...)
  components/common ConceptSearchInput, ErrorState, etc.
  lib/api/          axios clients + normalized OpenMRS error formatter
  lib/openmrs/      runtime config + IDGen helper
  lib/hooks/        cross-feature hooks (useDebouncedValue, ...)
  lib/query/        TanStack Query client config
  stores/           zustand auth + UI stores
  test/             Vitest setup
  types/            shared OpenMRS / form schema types
e2e/                Playwright specs
```

## Notable production-readiness changes

See [/CHANGELOG.md](../CHANGELOG.md) for the full list. Highlights:

- IDGen integration in patient registration (P0 fix).
- All mutations surface OpenMRS error messages with field-level mapping.
- Vitals / notes / lab orders are bound to an active visit before submission.
- Native OpenMRS session login replaces the static admin Basic-auth header.
- Concept search pickers replace UUID inputs.
- Strict CSP and other security headers ship from nginx.
- 54 Vitest tests + Playwright E2E specs.

## Skills tier

- Node 22 LTS, TypeScript 5+ (with the 6.0 deprecations flag), Vite 5.
- TailwindCSS 4 with shadcn-style primitives in `src/components/ui`.
- TanStack Query for server state, react-hook-form + Zod for client state.
