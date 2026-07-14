# Completed Evidence Coverage

This file summarizes the completed technical evidence package used by the public report.

## Knowledge Base Counts

- WHO/MSF chunk files: 401.
- WHO/MSF chunks: 69,476.
- WHO/MSF Qdrant snapshot: 448.8 MB.
- CIEL concepts: 58,687.
- CIEL mappings: 298,905.
- CIEL Q-and-A edges: 8,545.
- CIEL concept-set edges: 3,259.
- CIEL Qdrant snapshot: 326.3 MB.

## Runtime and Workflow Coverage

- Single-container runtime implemented with OpenMRS, MariaDB, Qdrant, TenaAgent, Gemma 4 E4B BF16 GGUF, WHO/MSF KB, and CIEL KB.
- Form builder implemented with WHO/MSF evidence review, CIEL resolution, coverage repair, schema build, clinician review, and OpenMRS publish.
- Scribe implemented with text and voice input, SOAP extraction, CIEL concept resolution, unresolved-item handling, and clinician confirmation.
- CDS implemented with agentic WHO/MSF guideline search and cited output formatting.
- Patient education implemented with evidence-grounded seven-section patient material generation.
- Report builder implemented with report spec drafting, deterministic CIEL/FHIR query compilation, and OpenMRS FHIR2 execution.

## Internal Technical Evaluation

- Form-builder baseline evaluation completed 147/147 prompts.
- Failure count: 0.
- Mean concept-cluster recall: 0.465.
- Schema-valid rate: 0.993.
- Median latency: 17.97 s.
- Form CIEL GEPA validation score: 0.246.
- Report GEPA validation score: 0.492.

## Adaptation Data Infrastructure

- Accepted form-builder requests: 4,932.
- Accepted report-builder requests: 2,441.
- Accepted scribe text samples: 3,581.
