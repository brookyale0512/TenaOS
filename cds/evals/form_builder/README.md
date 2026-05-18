# Form-builder evaluation

This directory holds the prompt set, gold-concept annotations, and the runner
used to measure the Gemma 4 form-builder agent against clinician-graded ground
truth. Numbers from this runner are the evidence the challenge submission
quotes ("for N prompts, the agent produces a CIEL-valid form covering X% of
the expected concept clusters").

## Files

- `prompts.json` — 10 form-build requests across ANC, TB, HIV, ENT, peds,
  vitals, diabetes, mental health, postnatal, and one French ANC prompt. Each
  prompt declares `requireAnyOf` clusters (the agent must include at least
  one CIEL concept from each cluster) plus a basket-size envelope.
- `run_eval.py` — dependency-free runner. Drives the live CDS service for
  each prompt, captures the full event log, scores the basket against gold,
  and writes a `summary.json` with the aggregate numbers.
- `runs/` — per-execution outputs; one directory per run, gitignored except
  for the headline `summary.json`.

## Running

Prerequisites:

1. CDS service is up: `curl http://127.0.0.1:8095/health` reports
   `vllm.healthy=true` and `ciel.available=true`.
2. OpenMRS is up enough to enumerate encounter types
   (`GET /forms/encounter-types` returns rows).

Run all prompts:

```bash
python cds/evals/form_builder/run_eval.py \
  --cds-base-url http://127.0.0.1:8095 \
  --prompts cds/evals/form_builder/prompts.json \
  --out cds/evals/form_builder/runs
```

Run a single prompt while iterating:

```bash
python cds/evals/form_builder/run_eval.py --filter ent-intake
```

The runner is fully read-only against OpenMRS: it never calls `publish_form`,
so eval drafts stay in `draft` status and can be cleaned up later.

## Metrics

For each prompt:

- **recall** — fraction of `requireAnyOf` clusters whose set intersects the
  basket. The headline number.
- **fieldCount** — basket question count.
- **sizeOk** — whether `fieldCount` falls inside the prompt's
  `minQuestions..maxQuestions` envelope.
- **schemaValid** — middleware reports `validation ok=true`.
- **drugDiagnosisRejectAttempts** — Drug/Diagnosis concepts the agent tried
  to add. Zero is the goal; the new middleware blocks them either way, but
  this metric tells us how much the model is fighting the filter.
- **toolCallCount** / **cielSearchCount** — agent loop efficiency.
- **elapsedSeconds** — end-to-end latency from draft creation to final
  basket.

Aggregate (`summary.json`):

- **meanRecall**, **schemaValidRate**, **medianElapsedSeconds**,
  **medianToolCalls**.

## Updating gold concepts

Gold concepts are intentionally a UNION across plausible CIEL matches because
CIEL has many synonyms. To extend a cluster, find the candidate concept ids
via the CIEL search service (or `cds/sources/.../ciel_search.sqlite3`) and
append them to `requireAnyOf[].conceptIds`. Do not narrow a cluster to a
single id unless a clinical SME has confirmed the CIEL canonical form.
