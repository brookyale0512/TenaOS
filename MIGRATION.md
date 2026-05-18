# Migration: ClinicDx Lite → TenaOS

The product was renamed from **ClinicDx Lite** (MedGemma challenge) to **TenaOS**
(Gemma 4 build). *Tena* (ጤና) is the Amharic word for "health".

The rename is a breaking change for any environment that already has a
running deployment, because Docker container/volume names, env-var prefixes,
the in-container filesystem layout, the OS user, and the OpenMRS healthcheck
user have all been renamed.

This document is the operational migration guide. Read it end-to-end before
upgrading a live environment.

---

## 1. Naming diff (what changed)

| Layer | Old | New |
|---|---|---|
| Product display name | `ClinicDx`, `ClinicDx Lite` | `TenaOS` |
| Frontend npm package | `clinicdx_frontend` | `tenaos_frontend` |
| Python logger | `clinicdx.cds` | `tenaos.cds` |
| Docker image | `clinicdx-lite-openmrs:latest`, `clinicdx-lite-cds:latest` | `tenaos-openmrs:latest`, `tenaos-cds:latest` |
| Docker container | `clinicdx_lite_openmrs`, `clinicdx_lite_cds` | `tenaos_openmrs`, `tenaos_cds` |
| Docker volumes | `clinicdx_lite_openmrs_data`, `clinicdx_lite_mariadb_data` | `tenaos_openmrs_data`, `tenaos_mariadb_data` |
| In-container path | `/opt/clinicDx/...` | `/opt/tenaos/...` |
| OS user inside container | `clinicdx:clinicdx` | `tenaos:tenaos` |
| Public-host env var | `CLINICDX_LITE_PUBLIC_HOST` | `TENAOS_PUBLIC_HOST` |
| Container-name env var | `CLINICDX_CONTAINER_NAME` | `TENAOS_CONTAINER_NAME` |
| Other `CLINICDX_*` script overrides | `CLINICDX_OPENMRS_BACKUP_DIR`, `CLINICDX_VERIFY_HTTP_*`, `CLINICDX_SEED_LOCATIONS`, `CLINICDX_LOGIN_LOCATION_TAG` | `TENAOS_OPENMRS_BACKUP_DIR`, `TENAOS_VERIFY_HTTP_*`, `TENAOS_SEED_LOCATIONS`, `TENAOS_LOGIN_LOCATION_TAG` |
| Shell helper file | `backend/scripts/lib/clinicdx-common.sh` | `backend/scripts/lib/tenaos-common.sh` |
| Shell function prefix | `clinicdx_*()` | `tenaos_*()` |
| Shell log prefix | `[clinicdx-lite]` | `[tenaos]` |
| OpenMRS healthcheck user | `clinicdx-healthcheck` | `tenaos-healthcheck` |
| Default DB password sample | `clinicdx_lite_openmrs` | `tenaos_openmrs` |
| Keycloak client id | `clinicdx-frontend` | `tenaos-frontend` |
| Frontend session-storage key | `clinicdx_token` | `tenaos_token` |
| Internal restart flag file | `/opt/openmrs/data/.clinicdx-managed-restart` | `/opt/openmrs/data/.tenaos-managed-restart` |
| Internal seed marker file | `/opt/openmrs/data/.clinicdx-locations-seeded` | `/opt/openmrs/data/.tenaos-locations-seeded` |
| OpenMRS managed config wrapper dir | `clinicdx-managed/` | `tenaos-managed/` |
| OpenMRS managed metadata IDs | `_clinicdx-managed-queue-service-set` | `_tenaos-managed-queue-service-set` |
| Workspace dir on disk | `/var/www/clinicdx_lite` | `/var/www/tenaos` |

---

## 2. Pre-migration: take a backup

```bash
cd /var/www/clinicdx_lite/backend
docker compose stop openmrs
docker run --rm \
  -v clinicdx_lite_openmrs_data:/from \
  -v "$(pwd)/../runtime-artifacts/openmrs/backups":/backup \
  alpine tar czf /backup/openmrs-data-pre-tenaos.tgz -C /from .
docker run --rm \
  -v clinicdx_lite_mariadb_data:/from \
  -v "$(pwd)/../runtime-artifacts/openmrs/backups":/backup \
  alpine tar czf /backup/mariadb-data-pre-tenaos.tgz -C /from .
```

Also dump the OpenMRS database SQL via the existing import-openmrs-db.sh
backup convention, or `mysqldump`, before proceeding.

---

## 3. .env update

Update every `.env` file referenced by your deployment:

```diff
- CLINICDX_LITE_PUBLIC_HOST=clinic.example.org
+ TENAOS_PUBLIC_HOST=clinic.example.org
```

Also rename any of these if you set them explicitly:
`CLINICDX_CONTAINER_NAME`, `CLINICDX_OPENMRS_BACKUP_DIR`,
`CLINICDX_VERIFY_HTTP_CONNECT_TIMEOUT_SECONDS`,
`CLINICDX_VERIFY_HTTP_MAX_TIME_SECONDS`,
`CLINICDX_SEED_LOCATIONS`, `CLINICDX_LOGIN_LOCATION_TAG`.

---

## 4. Migrate Docker volumes

Docker does not support renaming volumes in place. Pick **one** of:

### Option A — Recreate from backup (recommended)

```bash
cd /var/www/clinicdx_lite/backend
docker compose down
docker volume create tenaos_openmrs_data
docker volume create tenaos_mariadb_data

# Copy data across
docker run --rm \
  -v clinicdx_lite_openmrs_data:/from \
  -v tenaos_openmrs_data:/to \
  alpine sh -c 'cd /from && tar cf - . | tar xf - -C /to'
docker run --rm \
  -v clinicdx_lite_mariadb_data:/from \
  -v tenaos_mariadb_data:/to \
  alpine sh -c 'cd /from && tar cf - . | tar xf - -C /to'

# Bring up under new names
docker compose up -d --build

# Verify before deleting the old volumes
./scripts/verify-lite.sh
docker volume rm clinicdx_lite_openmrs_data clinicdx_lite_mariadb_data
```

### Option B — Fresh database, restore via SQL dump

```bash
docker compose down --volumes  # WARNING: destroys data
docker compose up -d --build
./scripts/import-openmrs-db.sh ../runtime-artifacts/openmrs/backups/<your-pre-tenaos-dump>.sql
```

Use Option B if your previous deployment was disposable (dev/staging) or if
the volume copy in Option A is too slow for your data size.

---

## 5. OpenMRS healthcheck user

The provisioning SQL now creates a user named `tenaos-healthcheck` (was
`clinicdx-healthcheck`). On a migrated database the **old user still exists**
and works. Either:

- Update `OPENMRS_HEALTHCHECK_USERNAME` in your `.env` to keep using the
  legacy `clinicdx-healthcheck` user (zero-downtime), then optionally rotate
  to `tenaos-healthcheck` later, **or**
- Re-apply [backend/metadata/healthcheck-user.sql](backend/metadata/healthcheck-user.sql)
  to create the new user, switch the env var to `tenaos-healthcheck`, then
  retire the old user via the OpenMRS admin UI.

```sql
-- Optional cleanup once the new user is verified:
UPDATE users SET retired = 1, retired_by = 1, date_retired = NOW(),
                 retire_reason = 'Renamed to tenaos-healthcheck (TenaOS rename)'
WHERE username = 'clinicdx-healthcheck';
```

---

## 6. Keycloak client id (only if Keycloak is enabled)

The frontend now sends `VITE_KEYCLOAK_CLIENT_ID=tenaos-frontend`. If your
realm only has a `clinicdx-frontend` client, sign-in will fail at deploy
time. Coordinate with the Keycloak admin to either:

- Add a new `tenaos-frontend` client (mirror the existing config) and
  retire `clinicdx-frontend` afterwards, or
- Override `VITE_KEYCLOAK_CLIENT_ID=clinicdx-frontend` in your deployment
  env and rename the realm client on a follow-up release.

---

## 7. Frontend session storage

The frontend session-token storage key changed from `clinicdx_token` to
`tenaos_token`. All currently signed-in users will be silently logged out
on first load of the new build. No data loss — they can sign in again.

---

## 8. Workspace path on the host

If you keep the repo at `/var/www/clinicdx_lite`, nothing breaks: the
in-repo paths still resolve. To complete the rename:

```bash
sudo systemctl stop <any-units-pointing-at-the-repo>
sudo mv /var/www/clinicdx_lite /var/www/tenaos
# Update any cron jobs / systemd units / IDE workspace files that hard-coded
# the old path.
```

The CDS Dockerfile (`cds/service/Dockerfile`) hard-codes
`/var/www/clinicdx_lite/cds` as the in-image working path. After the host
`mv`, rebuild the CDS image so its `CDS_ROOT` env reflects the new path
(`docker compose build --no-cache cds`). Note: the in-image path does not
have to match the host path; it only matters that the image is rebuilt
from the renamed working tree.

---

## 9. Verify

```bash
cd /var/www/tenaos/backend  # or /var/www/clinicdx_lite/backend if you skipped step 8
docker compose ps    # expect tenaos_openmrs, tenaos_cds
./scripts/verify-lite.sh
```

In the frontend, check:

- Browser tab title is **TenaOS**
- Login page shows the **TenaOS** wordmark and the *AI-native clinical
  operating system* tagline
- Sidebar wordmark reads **TenaOS** with a `T` monogram

If anything still says "ClinicDx", file an issue — Phase 5 of the rename
sweeps for residuals but logs and historical artifacts are intentionally
left in place.

---

## 10. What was deliberately not renamed

- CSS variable namespace `--clinic-*` (still in use; see `frontend/src/index.css`).
  "Clinic" is a generic descriptor, not a brand.
- Historical service logs in `cds/runtime/cds-service.log*` — stale data.
- SQL backups under `runtime-artifacts/openmrs/backups/` — immutable artifacts.
- The legacy MedGemma container reference `ClinicDx_backend` in
  `backend/lmic_emr_os/runtime_apply.py` — that targets the *previous*
  product, not this one.
- The OpenMRS internal database name `openmrs` — OpenMRS convention.
- Python module/package names (`cds_service`, `lmic_emr_os`) — internal,
  not brand-bearing.
