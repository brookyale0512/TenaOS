# TenaOS Evaluation Protocol

This protocol defines how TenaOS reports completed technical and analytical evidence without overstating clinical readiness.

## Result Labels

- **Measured:** reproduced from a checked-in artifact or local build artifact.
- **Implemented:** present in source and deployment configuration.
- **Internal technical evaluation:** internal run, calibration set, or technical benchmark artifact used as engineering evidence.

## Form Builder / GEPA Evaluation

Primary question: does GEPA improve the production form-builder pipeline without weakening deterministic safety?

Completed evidence:

- Internal form-builder evaluation completed 147/147 prompts with 0 failures.
- Mean concept-cluster recall was 0.465.
- Schema-valid rate was 0.993.
- Median latency was 17.97 s.
- GEPA form CIEL validation score was 0.246 on a small CIEL-focused subset.
- GEPA report validation score was 0.492 on a small report-builder subset.

Metrics:

- CIEL-expanded recall: fraction of gold clinical concept clusters represented by equivalent CIEL concepts.
- Exact cluster recall: exact CIEL ID overlap.
- Schema-valid rate: fraction of drafts with a built OpenMRS schema and no error-severity validation issues.
- Size-ok rate: committed field count inside the expected range.
- Hallucinated-code rate: committed CIEL IDs that do not resolve locally.
- Retired-code rate: committed CIEL IDs marked retired.
- Tool calls and latency.
- Publish readiness: schema valid, no hallucinated/retired codes, clinician-reviewable draft.

Promotion gate:

- Optimized prompts must improve mean CIEL-expanded recall.
- Schema-valid rate must remain 1.0.
- Hallucinated-code and retired-code counts must remain zero.
- No increase in unsafe apply-time rejection reasons.

## WHO/MSF Retrieval Evaluation

Primary question: does the local guideline KB return actionable, on-condition, cited evidence?

Completed evidence:

- 401 chunk JSONL files.
- 69,476 guideline chunks.
- 448.8 MB local Qdrant snapshot.
- Runtime supports lexical BM25, semantic EmbedGemma, and reciprocal-rank fusion.
- Reranker includes synonym expansion, content-type boosts, actionability scoring, domain coherence, source diversity, and low-confidence flags.

Metrics:

- Top-k relevance by clinical reviewer or gold chunk labels.
- Citation coverage: final CDS/material output includes at least one retrieved source for every recommendation.
- Unsupported-answer abstention: questions outside the KB evidence are handled through abstention and clinician review.
- Actionable-hit rate: top results include recommendation or implementation chunks where the query asks for treatment, dose, or referral.
- Off-condition top-hit rate.
- Latency.

## CIEL Retrieval and Validation Evaluation

Primary question: does CIEL search recover clinically intended concepts while preventing unsafe mappings?

Completed evidence:

- CIEL SQLite contains 58,687 concepts.
- CIEL SQLite contains 298,905 mappings.
- CIEL SQLite contains 8,545 Q-and-A edges and 3,259 concept-set edges.
- Every concept has a hydrated bundle and search text.
- Qdrant semantic index uses SapBERT plus BM25 and hydrates from SQLite.

Metrics:

- Top-1 and top-5 concept recall.
- Datatype correctness.
- Class correctness.
- Retired concept rejection.
- Duplicate-code rejection for distinct fields.
- SQLite hydration success.
- OpenMRS UUID derivation success.

## Scribe Evaluation

Primary question: can TenaOS turn notes into reviewable structured clinical records without silently saving unresolved items?

Completed evidence:

- Text and voice scribe routes are implemented.
- Voice route converts uploaded audio to 16 kHz mono WAV and sends it through Gemma audio input.
- Amharic text path translates notes before SOAP extraction.
- LoRA/SFT corpus includes text, voice, and Amharic traces for adapter training.

Metrics:

- SOAP section completeness.
- Diagnosis/concept precision and recall.
- Observation value/unit accuracy.
- Medication extraction accuracy for drug, dose, route, and frequency.
- Unresolved-item honesty.
- PHI leakage rejection.
- Clinician edit distance.

## Report Builder Evaluation

Primary question: can natural-language reporting questions compile into deterministic FHIR query plans and correct counts?

Completed evidence:

- Deterministic report compiler is implemented.
- Compiler validates CIEL concepts before FHIR execution.
- Filter modes cover Boolean, coded, numeric, condition, and any-value observations.
- OpenMRS FHIR2 reader executes datatype-specific filtering.

Metrics:

- Report intent classification.
- Date range extraction.
- CIEL filter selection.
- Query plan validity.
- FHIR execution success.
- Count agreement with known fixtures.
- Unsupported-query refusal.

## CDS and Patient Education Evaluation

Primary question: are recommendations and patient materials grounded, cited, useful, and appropriately limited?

Completed evidence:

- CDS workflow performs agentic search over the local WHO/MSF KB.
- Patient education workflow produces seven-section patient-facing material from retrieved evidence.
- Both workflows stream or persist traces for auditability.

Metrics:

- Retrieval recall.
- Citation coverage.
- Guideline faithfulness.
- Unsupported-answer abstention.
- Patient readability.
- Clinician safety review.

## Deployment Evaluation

Primary question: can a clinic-owned server run the full stack locally?

Completed evidence:

- Single-container runtime is implemented.
- Gemma 4 E4B BF16 GGUF and multimodal projector are the release model artifacts.
- WHO/MSF Qdrant snapshot is 448.8 MB.
- CIEL Qdrant snapshot is 326.3 MB.
- CIEL SQLite artifact is approximately 1.7 GB.

## Clinical Governance Boundary

These benchmarks document the completed technical and analytical evaluation package for clinician-in-the-loop operation.
