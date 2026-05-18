# SMART DAK TB Phase 1 Experiments

Date: 2026-05-10

This folder contains synthetic Phase 1 artifacts for testing whether Gemma 4 can use tools to navigate from OpenMRS-style patient facts to WHO DAK TB decision logic and then to grounded CDS output.

## Current Scope

Decision table:

- `TB.B4.DT`: Screening algorithm

Source:

- `/var/www/tenaos/cds/sources/smart-dak-tb-downloads/TB DAK_decision-support logic.xlsx`

## Scenarios

| Scenario | Patient bundle | Trace | CDS card | Expected behavior |
|---|---|---|---|---|
| Adult non-PLHIV household contact | `sample-patient-bundles/tb-b4-adult-contact.bundle.json` | `tool-call-traces/tb-b4-adult-contact.trace.json` | `generated-cds-cards/tb-b4-adult-contact.card.json` | Tool sequence reaches deterministic DAK result and produces a grounded CDS card. |
| Missing risk group | `sample-patient-bundles/tb-b4-missing-risk-group.bundle.json` | `tool-call-traces/tb-b4-missing-risk-group.trace.json` | `generated-cds-cards/tb-b4-missing-risk-group.card.json` | Tool sequence stops before rule execution and produces an insufficient-data card. |

## What This Proves

- Gemma can be evaluated on multi-step tool choice without letting it produce clinical advice from weights.
- Missing-data behavior is explicit.
- Final CDS cards can be grounded in deterministic DAK output or deterministic insufficient-data output.

## What This Does Not Prove Yet

- The spreadsheet rule executor has not been implemented.
- CQL and PlanDefinition execution are not available for this DAK yet.
- No OpenMRS integration has been touched.
- These synthetic outputs are not clinically validated.
