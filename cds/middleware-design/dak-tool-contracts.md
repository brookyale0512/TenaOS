# DAK Middleware Tool Contracts

Date: 2026-05-10

## Purpose

These are the first tool contracts for Gemma 4 multi-step use of WHO DAK artifacts. The contracts are intentionally narrow. They let Gemma decide which safe tool to call next, while deterministic middleware owns all clinical logic.

The initial target is WHO SMART DAK TB, starting with `TB.B4.DT`.

## Global Rules

Gemma 4 may:

- Select an allow-listed tool.
- Provide structured arguments.
- Ask for missing data based on tool responses.
- Present final CDS using only returned tool data.

Gemma 4 must not:

- Execute clinical logic internally.
- Use model memory for TB recommendations.
- Call generic shell, database, or unrestricted search tools.
- Write orders, observations, diagnoses, or tasks to OpenMRS.
- Invent missing patient facts or site configuration.

All tool calls must be:

- Allow-listed by name.
- JSON-schema validated.
- Read-only for Phase 1.
- Fully logged with arguments, result, DAK source version, and timestamp.

## Common Response Envelope

All tools return this envelope:

```json
{
  "ok": true,
  "tool": "tool_name",
  "source": {
    "dak": "smart-dak-tb",
    "version": "1.0.2-ci-build",
    "commit": "08ac630a29062eecee2c5ec0c6811c2e8343b2c5",
    "artifact": "TB DAK_decision-support logic.xlsx"
  },
  "data": {},
  "warnings": [],
  "errors": []
}
```

If `ok` is false, Gemma must not produce clinical CDS. It may summarize the error or ask for missing inputs.

## Tool 1: `search_dak_rules`

Find candidate DAK decision rules for a patient/workflow context.

Input:

```json
{
  "dak": "smart-dak-tb",
  "workflowContext": {
    "activityId": "TB.B4",
    "clinicalArea": "screening",
    "userIntent": "determine screening algorithm"
  },
  "patientSummary": {
    "ageYears": 35,
    "knownFacts": ["risk group"]
  }
}
```

Output `data`:

```json
{
  "candidates": [
    {
      "decisionId": "TB.B4.DT",
      "title": "Screening algorithm",
      "trigger": "TB.B4. Determine the screening algorithm",
      "hitPolicy": "Rule order",
      "reasonSelected": "Workflow activity directly matches trigger."
    }
  ]
}
```

Safety:

- Must return decision metadata only, not full rule tables.
- Must not return recommendations.

## Tool 2: `get_required_data`

Return the required and conditionally useful input facts for a decision ID.

Input:

```json
{
  "dak": "smart-dak-tb",
  "decisionId": "TB.B4.DT"
}
```

Output `data`:

```json
{
  "decisionId": "TB.B4.DT",
  "requiredFacts": [
    {
      "id": "age",
      "label": "Age",
      "type": "quantity",
      "unit": "years",
      "openMrsFhirSource": "Patient.birthDate",
      "required": true
    },
    {
      "id": "riskGroup",
      "label": "Risk group",
      "type": "coded",
      "openMrsFhirSource": "Observation or program data mapped to TB risk group",
      "required": true
    }
  ],
  "configurationFacts": []
}
```

Safety:

- Must distinguish patient facts from site configuration.
- Must return normalized fact IDs used by downstream tools.

## Tool 3: `fetch_patient_fhir`

Fetch only the FHIR resources needed for the requested data profile.

Input:

```json
{
  "patientId": "example-patient-001",
  "requiredFactIds": ["age", "riskGroup"],
  "fhirServerProfile": "openmrs-fhir-r4"
}
```

Output `data`:

```json
{
  "bundle": {
    "resourceType": "Bundle",
    "type": "collection",
    "entry": []
  },
  "fetchedResources": ["Patient", "Observation"],
  "omittedResources": []
}
```

Safety:

- Must use narrow FHIR queries.
- Must not fetch unrelated chart history.
- Must redact or omit unnecessary PHI before sending data to Gemma.

## Tool 4: `normalize_to_dak_inputs`

Convert OpenMRS FHIR resources and site config into normalized DAK inputs.

Input:

```json
{
  "decisionId": "TB.B4.DT",
  "fhirBundleRef": "trace://bundle/example-patient-001",
  "siteConfigRef": "trace://site/default"
}
```

Output `data`:

```json
{
  "decisionId": "TB.B4.DT",
  "normalizedInputs": {
    "age": {
      "value": 35,
      "unit": "years",
      "source": "Patient.birthDate"
    },
    "riskGroup": {
      "value": "household_contact",
      "display": "Household contact of a person with TB",
      "source": "Observation/tb-risk-group"
    }
  },
  "unmappedFacts": []
}
```

Safety:

- Must record source resource IDs for every normalized fact.
- Must not infer missing facts.
- Must return `unmappedFacts` when local OpenMRS concepts cannot be mapped.

## Tool 5: `detect_missing_data`

Check whether a decision can be evaluated.

Input:

```json
{
  "decisionId": "TB.B4.DT",
  "normalizedInputs": {
    "age": { "value": 35, "unit": "years" }
  }
}
```

Output `data`:

```json
{
  "decisionId": "TB.B4.DT",
  "canEvaluate": false,
  "missingFacts": [
    {
      "id": "riskGroup",
      "label": "Risk group",
      "reason": "Required input column for TB.B4.DT"
    }
  ]
}
```

Safety:

- If `canEvaluate` is false, Gemma may not call `apply_dak_rule`.
- Missing facts must be visible in final output if the workflow stops.

## Tool 6: `apply_dak_rule`

Execute deterministic DAK rule matching. This is the clinical decision tool.

Input:

```json
{
  "decisionId": "TB.B4.DT",
  "normalizedInputs": {
    "age": { "value": 35, "unit": "years" },
    "riskGroup": { "value": "household_contact" }
  }
}
```

Output `data`:

```json
{
  "decisionId": "TB.B4.DT",
  "sourceSheet": "TB.B4.DT Screening algorithm",
  "hitPolicy": "Rule order",
  "matchedRows": [
    {
      "rowNumber": 7,
      "inputMatch": {
        "riskGroup": "household_contact",
        "age": ">= 15 years"
      },
      "outputType": "Action",
      "action": "Use the adult TB screening algorithm.",
      "guidance": "Proceed with the screening algorithm indicated by the DAK table.",
      "annotations": null,
      "references": []
    }
  ],
  "clinicalResultStatus": "rule_matched"
}
```

Safety:

- Must be deterministic and independently testable.
- Must preserve source row number(s).
- Must return `clinicalResultStatus: insufficient_data` rather than guessing.
- Must not use Gemma.

## Tool 7: `get_rule_rationale`

Fetch source metadata and rationale for a matched result.

Input:

```json
{
  "decisionId": "TB.B4.DT",
  "matchedRows": [7]
}
```

Output `data`:

```json
{
  "decisionId": "TB.B4.DT",
  "businessRule": "Based on the client's age and risk group, determine the recommended TB screening algorithm.",
  "trigger": "TB.B4. Determine the screening algorithm",
  "references": [],
  "sourceCitation": "WHO SMART DAK TB decision-support logic workbook, sheet TB.B4.DT Screening algorithm"
}
```

Safety:

- Must return source/rationale only.
- Must not add model-generated clinical rationale.

## Tool 8: `create_cds_card`

Convert deterministic rule result into a structured CDS card object. Gemma may draft wording, but the card builder validates required grounding.

Input:

```json
{
  "ruleResultRef": "trace://result/tb-b4-example-001",
  "style": "clinician_concise",
  "draftText": {
    "summary": "TB screening algorithm is indicated based on DAK rule TB.B4.DT.",
    "detail": "The patient is a household contact and is 35 years old. The DAK rule matched row 7."
  }
}
```

Output `data`:

```json
{
  "card": {
    "summary": "Use DAK-selected TB screening algorithm",
    "indicator": "info",
    "detail": "Based only on WHO SMART DAK TB rule TB.B4.DT, the provided age and risk group matched the screening-algorithm rule. Review the suggested screening workflow before proceeding.",
    "source": {
      "label": "WHO SMART DAK TB TB.B4.DT",
      "url": "https://worldhealthorganization.github.io/smart-dak-tb/decision-logic.html"
    }
  },
  "groundingCheck": {
    "passed": true,
    "unsupportedStatements": []
  }
}
```

Safety:

- Must fail if draft text mentions facts absent from `ruleResultRef`.
- Must include source label and artifact version.
- Must allow "insufficient data" cards.

## Tool 9: `log_trace`

Persist the end-to-end trace for audit.

Input:

```json
{
  "traceId": "tb-b4-example-001",
  "patientRef": "synthetic/example-patient-001",
  "toolCalls": [],
  "finalStatus": "cds_card_created"
}
```

Output `data`:

```json
{
  "traceId": "tb-b4-example-001",
  "stored": true
}
```

Safety:

- Must avoid storing unnecessary PHI in Phase 1 traces.
- Must preserve source artifact/version and matched row.

## Expected Gemma 4 Call Sequence

Happy path:

1. `search_dak_rules`
2. `get_required_data`
3. `fetch_patient_fhir`
4. `normalize_to_dak_inputs`
5. `detect_missing_data`
6. `apply_dak_rule`
7. `get_rule_rationale`
8. `create_cds_card`
9. `log_trace`

Missing-data path:

1. `search_dak_rules`
2. `get_required_data`
3. `fetch_patient_fhir`
4. `normalize_to_dak_inputs`
5. `detect_missing_data`
6. `create_cds_card` with insufficient-data status
7. `log_trace`

## Final CDS Output Requirements

Every final CDS card must include:

- Decision ID.
- DAK source/version.
- Matched row(s), or missing facts if not evaluated.
- Patient facts used.
- Action/guidance returned by deterministic middleware.
- Clear wording that this is decision support, not an autonomous order.

Every final CDS card must exclude:

- Advice not present in deterministic DAK result.
- Model confidence scores.
- Hidden assumptions.
- Unverified local policy claims.
- Unverified medication dosing or regimen claims.

## Phase 1 Implementation Note

These contracts are design contracts only. The next implementation step is a read-only parser/executor for `TB.B4.DT`, followed by synthetic traces that simulate these tool calls.
