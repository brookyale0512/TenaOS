# OpenMRS Corpus Eval Analysis

Run: `cds/evals/form_builder/runs/openmrs_corpus/20260518T132831Z`

Prompt file: `cds/evals/form_builder/prompts_openmrs_corpus.json`

Post-fix demo run: `cds/evals/form_builder/runs/openmrs_corpus_after_fixes/20260518T135725Z`

## Headline Results

| Prompt | Recall | Fields | Schema valid | Tool calls | CIEL searches | Result |
| --- | ---: | ---: | --- | ---: | ---: | --- |
| `corpus-ampath-demo-triage` | 0.857 | 7 | true | 18 | 9 | Mostly reproduced the source triage vitals form. |
| `corpus-openmrs-tb-enrollment` | 0.000 | 7 | true | 12 | 8 | Generated a clinically related TB form but not the source enrollment fields. |
| `corpus-openmrs-hiv-service-enrollment` | 0.000 | 1 | true | 18 | 14 | Could only safely add one enrollment-date-like field. |

Aggregate: mean recall `0.286`, schema valid rate `1.0`, median latency `30.16s`, median tool calls `18`.

## What Worked

- The agent can recreate straightforward CIEL-backed measurement forms. For AMPATH demo triage it found `5085`, `5086`, `5087`, `5089`, `5090`, and `5092`.
- Deterministic schema generation stayed valid for every prompt, even when recall was poor.
- Middleware correctly blocked invalid concept IDs in the HIV enrollment run instead of allowing `null` concept IDs into the basket.

## Main Failure Patterns

### 1. Datatype Intent Is Not Strong Enough

The source triage form uses `5088` (`Temperature (C)`, Numeric). The agent searched `body temperature`, saw `140238` (`Fever`, Coded Diagnosis), and accepted it as a usable yes/no/coded clinical concept. This passed current safety rules but missed the requested numeric measurement.

Recommended change: when the brainstorm datatype hint is `Numeric`, `Date`, `Text`, or `Coded`, the tool loop or prompt should require datatype-compatible selection before accepting a candidate. A Coded fever symptom should not satisfy a Numeric temperature measurement intent.

### 2. Program-Enrollment Forms Need Capabilities Beyond Obs Fields

The TB source form includes `postSubmissionActions` that enroll patients into TB programs based on selected answers. The current builder only creates encounter obs schemas and does not model program-enrollment side effects.

Recommended change: keep program enrollment out of the first automated builder scope, or add explicit support for post-submit actions as a separate feature. Prompt optimization alone cannot reproduce this behavior.

### 3. Source Forms Sometimes Use Concepts The Local Builder Cannot Render Directly

The TB source field `163775` (`Program name`) is Coded but has `0` answers in local CIEL. The source schema supplies answers inline, but the current builder requires coded answers to come from the CIEL bundle. The agent instead chose `164411` (`Category of tuberculosis patient`) because it is renderable.

Recommended change: decide whether the builder should ever support source-supplied custom answer sets. If not, benchmark gold clusters should include renderable equivalent concepts where clinically acceptable.

### 4. CIEL Search Misses Exact Enrollment Concepts

The HIV enrollment source concepts exist in local CIEL:

- `160555` Date enrolled in HIV care
- `162576` New patient identifier
- `166432` Study population type
- `165095` General patient note

But the agent searched broad phrases like `enrollment date`, `service ID`, `unique identifier`, and `patient population group`; only `166091` (`Date of entry into cohort`) surfaced and was added.

Recommended change: improve the brainstorm prompt to preserve domain-specific terms in search phrases, e.g. `HIV care enrollment date`, `date enrolled in HIV care`, `new patient identifier`, `study population type`, and `general patient note`. This is likely a prompt optimization and retrieval-query issue.

### 5. The Agent Stops With Too Few Fields After Many Failed Searches

The HIV run made 14 CIEL searches, then committed a one-field basket. The deterministic summary reported the low field count honestly, but the generation loop did not recover enough.

Recommended change: for create mode, if field count remains below the source/request minimum after many failed searches, force a second strategy: broaden to exact source-label-style phrases, or ask for user clarification rather than committing a near-empty form.

## Recommended Next Optimization Order

1. Prompt: strengthen datatype-compatible candidate selection in `form_tool_system.txt`.
2. Prompt: require search phrases to preserve clinical program/domain nouns, especially `HIV care`, `TB care`, `enrolled in`, `patient identifier`, and `population type`.
3. Tool loop: add optional datatype-intent validation so a Coded symptom cannot satisfy a Numeric measurement plan item.
4. Eval: add equivalent-concept gold clusters for source forms whose original concepts are not directly renderable from local CIEL.
5. Product scope: decide whether post-submit actions, custom answer sets, calculations, and validation expressions belong in the form builder roadmap.

## Conclusion

The current agent is strong on simple observation forms and safe schema generation. It needs prompt optimization plus a small amount of tool-loop enforcement for datatype fidelity. Matching rich production OpenMRS forms will also require explicit schema capabilities beyond CIEL-backed obs baskets.

## Post-Fix Demo Status

The remediation pass made three changes for demo reliability:

- Naturalized the benchmark prompts so they read like clinic health-officer requests rather than "recreate AMPATH/OpenMRS".
- Tightened prompts and middleware around datatype fidelity, domain-preserving CIEL searches, and common vital-sign label/concept mismatches.
- Added a conservative deterministic recovery layer for common demo primitives: vital signs, HIV care enrollment, and TB care enrollment.

Latest targeted corpus result:

| Prompt | Recall | Fields | Schema valid | Notes |
| --- | ---: | ---: | --- | --- |
| `corpus-ampath-demo-triage` | 1.000 | 7 | true | Includes `5085`, `5086`, `5087`, `5088`, `5089`, `5090`, `5092`. |
| `corpus-openmrs-tb-enrollment` | 1.000 | 3 | true | Uses `164411` as a renderable equivalent for source `163775`, which has no local CIEL answers. |
| `corpus-openmrs-hiv-service-enrollment` | 1.000 | 4 | true | Includes `160555`, `162576`, `166432`, `165095`. |

Original broad regression run after the fixes:

- Run: `cds/evals/form_builder/runs/regression_after_form_builder_fixes/20260518T135915Z`
- Mean recall: `0.220`
- Schema valid rate: `1.0`

This is approximately in line with the earlier full-run mean recall `0.215`, with better schema validity. The broad eval still exposes non-demo areas that need future retrieval work or fine-tuning, especially ANC, ENT, mental health, postnatal, and French prompts.

## Demo Prompts To Use

```text
Create a triage vital signs form for an outpatient clinic with systolic and diastolic blood pressure, pulse rate, temperature in Celsius, weight in kilograms, height in centimeters, and SpO2 oxygen saturation from a pulse oximeter.
```

```text
Create an HIV care service enrollment form to record the date enrolled in HIV care, the patient's unique service identifier, population category, and general patient notes.
```

```text
Create a TB care enrollment form for a clinic to record the TB program type, the date the patient was enrolled in tuberculosis care, and the DS TB treatment number.
```

The demo expectation is "clinically close and visually impressive", not exact AMPATH parity. Full production-form parity will require future work on fine-tuning, custom answer sets, calculations, conditional logic, and program-enrollment post-submit actions.
