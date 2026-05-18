# Operational Analysis

The control plane now performs a second layer of analysis on top of structural schema validation. This analysis focuses on patient flow, queue topology, payment gates, dispensing assumptions, and KPI-ready workflow metadata.

## Implementation

- analyzer: `lmic_emr_os/operational_analysis.py`
- preview integration: `lmic_emr_os/onboarding.py`
- CLI command: `python -m lmic_emr_os.cli analyze-operations <config>`

## What It Produces

For any clinic bundle, the analyzer computes:

- start queues
- terminal queues
- isolated queues
- unreachable queues
- cycle detection in queue routing
- route simulations from each start queue
- KPI-ready metric definitions
- Mermaid workflow graph output
- operational warnings and errors

## Current Rule Set

The current analysis checks for:

- missing starting queue in the routing graph
- isolated or unreachable queues
- routing cycles
- payment-gated routes without cashier/payment-mode/cash-point support
- dispensing queues without stock locations
- enabled lab/imaging domains without corresponding routing queues

## Bundle Outputs

When a change bundle is built, it now includes:

- `operational-analysis.json`
- `workflow-graph.mmd`

These files sit beside the existing `preview.json`, plans, and OpenMRS pack.

## Example CLI Usage

```bash
python -m lmic_emr_os.cli analyze-operations \
  examples/emr-os/archetypes/hospital-composition.json
```

## Why This Matters

This phase moves the system from "can compile metadata" to "can reason about clinic flow." It gives operators and future agent UX a concrete way to review whether a bundle produces the intended patient movement before any live apply happens.
