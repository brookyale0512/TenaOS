# TenaAgent/scripts

Operator-facing helpers for the Gemma-driven form + report builder.

## `seed_demo_obs.py`

Populates the running OpenMRS with 24 synthetic patients plus weighted obs
across every CIEL concept referenced by any published form. Without this,
the report builder returns zero hits for every query because no encounters
have been filled through the form-builder forms yet.

### Run

```bash
OPENMRS_USERNAME=admin OPENMRS_PASSWORD=Admin123 \
    python3 TenaAgent/scripts/seed_demo_obs.py
```

Expected output:

```
[seed] Discovered 11 CIEL concepts across published forms.
[seed] Seeded 24 patients, ~70 encounters, ~300 obs across 11 CIEL concepts.
```

The script is idempotent — synthetic patients are looked up by identifier
prefix `SYN-DEMO-NNN` before being re-created. Distributions are
reproducible (PRNG seed `42`). Weighted obs profile (TB-like for the demo):

- Cough variants: ~60% Yes
- Weight loss: ~40% Yes
- Fever: ~30% Yes
- Night sweats: ~25% Yes
- HIV status: 65% Negative / 20% Positive / 15% Unknown
- Numeric obs: pulled from the CIEL `extras.low_absolute` / `hi_absolute`
  range

### Wipe

```bash
OPENMRS_USERNAME=admin OPENMRS_PASSWORD=Admin123 \
    python3 TenaAgent/scripts/seed_demo_obs.py --wipe
```

The REST-side wipe iterates the patient search for `SYN-DEMO` and voids
matches. Operators with direct DB access can wipe faster:

```sql
DELETE FROM patient_identifier WHERE identifier LIKE 'SYN-DEMO-%';
```

(Or void via your usual OpenMRS cleanup path.)

### Why this exists

Reports only cover data captured through the CIEL-backed forms in this
system. A fresh install has no obs data; the seed script is what lets the
demo show non-trivial numbers from minute one.
