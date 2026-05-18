# Archetypes

Three archetype bundles are included under `examples/emr-os/archetypes/` so the control plane can be exercised against realistic facility patterns.

## Included Archetypes

- `general-outpatient-clinic.json`
  - Community OPD with registration, triage, consultation, cashier, pharmacy, optional lab handoff, and basic billing.
- `specialty-clinic.json`
  - Maternal health / ANC-oriented specialty clinic with specialty forms, program workflow structure, lab support, and imaging policy hooks.
- `hospital-composition.json`
  - District hospital modeled as a composition of departments, shared cashier/pharmacy/lab/radiology resources, and cross-department patient routing.

## What These Bundles Prove

- one config model can represent both simple clinics and multi-department hospitals
- the compiler can emit direct OpenMRS metadata packs from each archetype
- the control plane can produce OpenELIS, Orthanc, and Keycloak plans from the same source bundle
- routing, billing, stock, and governance can be carried in one validated object even when application differs by domain

## Suggested Verification Flow

1. Validate the archetype.
2. Preview the bundle.
3. Analyze the operational workflow.
4. Compile the OpenMRS pack.
5. Build the full change bundle.
6. Review `operational-analysis.json`, `workflow-graph.mmd`, `apply-order.json`, and `rollback.json`.

Example:

```bash
python -m lmic_emr_os.cli validate-config examples/emr-os/archetypes/general-outpatient-clinic.json
python -m lmic_emr_os.cli preview examples/emr-os/archetypes/general-outpatient-clinic.json
python -m lmic_emr_os.cli analyze-operations examples/emr-os/archetypes/general-outpatient-clinic.json
python -m lmic_emr_os.cli compile-openmrs examples/emr-os/archetypes/general-outpatient-clinic.json /tmp/sunrise-pack
python -m lmic_emr_os.cli build-change-bundle examples/emr-os/archetypes/general-outpatient-clinic.json /tmp/change-bundles
```

## Why Archetypes Matter

The setup product should not start from a blank slate for every facility. These archetypes are the first backend implementation of the roadmap principle: start from a reusable facility pattern, then let the conversational agent apply local deltas.
