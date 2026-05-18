# Department Library And Composition Engine

The control plane now includes a reusable department-pack library and a composition engine that can assemble a hospital or specialty facility into a normal `ClinicConfigModel`.

## Implementation

- engine: `lmic_emr_os/department_composition.py`
- pack library: `examples/emr-os/department-packs/`
- composition plans: `examples/emr-os/compositions/`
- CLI:
  - `python -m lmic_emr_os.cli list-department-packs`
  - `python -m lmic_emr_os.cli compose-hospital <plan> <output>`

## Included Department Packs

- `opd-core`
- `cashier-shared`
- `pharmacy-shared`
- `laboratory-shared`
- `imaging-shared`
- `maternal-health`

## Included Composition Plans

- `examples/emr-os/compositions/district-hospital-plan.json`
- `examples/emr-os/compositions/maternal-health-centre-plan.json`

## Composition Model

Each composition plan contains:

- `baseConfig`
  - shared facility identity, governance, identity model, and any global service settings
- `packSelections`
  - selected reusable department packs with namespaces and mount locations
- `crossPackRoutingRules`
  - routing links between packs such as `opd -> cashier -> pharmacy`
- `overlays`
  - final bundle-specific additions like pricing rules or stock rules

## Design Rules

- department packs are partial clinic-config contributions, not a second config language
- all pack-local IDs are namespaced during composition
- cross-pack routing is declared in the composition plan
- the output is a standard `ClinicConfigModel`, so the rest of the control plane stays unchanged

## Example

```bash
python -m lmic_emr_os.cli compose-hospital \
  examples/emr-os/compositions/district-hospital-plan.json \
  /tmp/composed-district-hospital.json

python -m lmic_emr_os.cli preview /tmp/composed-district-hospital.json
python -m lmic_emr_os.cli build-change-bundle /tmp/composed-district-hospital.json /tmp/change-bundles
```

## Current Boundary

This is a real composition engine, but not yet a full orchestration engine. It assembles reusable departments into a valid bundle; it does not yet manage advanced inter-department policy resolution, shared-capacity planning, or dynamic conflict negotiation between packs.
