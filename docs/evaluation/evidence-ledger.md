# TenaOS Evidence Ledger

This ledger separates the implemented, measured, and internal technical evaluation claims used in the completed public report.

## Claim Labels

- **Measured:** directly backed by checked-in artifacts or local build artifacts.
- **Implemented:** present in source and deployment configuration.
- **Internal technical evaluation:** internal run, calibration set, or technical benchmark artifact used as engineering evidence.

## Source-Backed Claims

| Claim | Status | Evidence |
| --- | --- | --- |
| TenaOS is a local-first clinical OS built on OpenMRS and Gemma 4 E4B. | Implemented | `README.md`, `docker-compose.yml`, `Dockerfile`, `docker/supervisord.conf` |
| Runtime serves one container with nginx, React, OpenMRS, MariaDB, Qdrant, Gemma via `llama.cpp`, TenaAgent, and KB daemons. | Implemented | `docker/supervisord.conf`, `docker/nginx.conf`, `docker/start-llama.sh`, `docker/start-kb.sh` |
| TenaAgent owns the LLM-mediated workflows and is the only component that talks to the model server. | Implemented | `TenaAgent/README.md`, `TenaAgent/service/tena_agent_service/app.py` |
| Model output does not write directly to OpenMRS; drafts are validated and clinician-approved. | Implemented | `TenaAgent/README.md`, `form_drafts.py`, `openmrs_writer.py`, `scribe_routes.py`, `report_builder.py` |
| WHO/MSF guideline retrieval uses local Qdrant hybrid search over dense EmbedGemma and sparse BM25 vectors. | Implemented | `TenaOS-KnowledgeBase/README.md`, `retrieval_core_v2.py`, `qdrant_retriever.py`, `embedder.py` |
| The WHO/MSF KB was built through a PDF to Pulse OCR to Docling to chunk/enrich/embed/Qdrant pipeline. | Implemented | `/var/www/TenaOS_DeepSeek/kb-pipeline/PIPELINE_README.md`, `pulse_extract.py`, `convert_md.py`, `chunkers/common.py`, `embed_chunks_gpu.py`, `build_qdrant.py` |
| WHO/MSF build artifacts include 401 chunk JSONL files and 69,476 chunks. | Measured | Local count of `/var/www/TenaOS_DeepSeek/kb-pipeline/source/chunks_output/*.jsonl` |
| The WHO/MSF Qdrant snapshot is about 448.8 MB. | Measured | `/var/www/TenaOS/qdrant-snapshots/who_msf_guidelines.snapshot` |
| CIEL SQLite contains 58,687 concepts from CIEL `v2026-03-23`. | Measured | `/var/www/TenaOS/TenaOS-CIEL/ciel_search.sqlite3`, `source_metadata` |
| CIEL SQLite contains 298,905 mappings, including 8,545 Q-and-A edges and 3,259 concept-set edges. | Measured | SQLite queries over `concept_mappings` |
| CIEL semantic search uses SapBERT dense vectors plus BM25 sparse vectors in Qdrant, then hydrates from SQLite. | Implemented | `TenaOS-CIEL/ciel_search/qdrant_index.py`, `TenaOS-KnowledgeBase/kb_guidelines/ciel_retriever.py`, `TenaOS-CIEL/ciel_search/service.py` |
| The CIEL Qdrant snapshot is about 326.3 MB. | Measured | `/var/www/TenaOS/qdrant-snapshots/ciel_concepts.snapshot` |
| GEPA optimization runs offline and optimizes prompts used by the real form pipeline. | Implemented | `scripts/optimization/run_form_gepa.py`, `scripts/optimization/form_pipeline_dspy.py`, `agent_prompts.py` |
| Prompt overlays are activated by `TENAOS_USE_OPTIMIZED_PROMPTS`; base prompts remain hash-pinned. | Implemented | `agent_prompts.py`, `TenaAgent/README.md` |
| Baseline form-builder evaluation completed 147/147 prompts with 0 failures, mean recall 0.465, schema-valid rate 0.993, median latency 17.97 s. | Internal technical evaluation | `/var/www/TenaOS_DeepSeek/evals/form_builder/baselines/2026-05-pre-sota/prompts_corpus_v4/20260523T013013Z/summary.json` |
| Form CIEL GEPA historical run reached best validation score 0.246 on a small dev subset. | Internal technical evaluation | `/var/www/TenaOS_DeepSeek/phase1_ciel_gepa/gepa/runs_v2/phase_a_v2_fix_20260526T1635Z/summary.json` |
| Report GEPA historical run reached best validation score 0.492 on a small report GEPA subset. | Internal technical evaluation | `/var/www/TenaOS_DeepSeek/phase1_ciel_gepa/gepa/runs_report_v1/phase_b_report_v1_20260526T2011Z/summary.json` |
| LoRA/SFT corpus includes 16,005 validated task-tagged traces and a 18,909 / 1,071 / 1,109 train-validation-test split. | Measured | Submitted Hugging Face artifacts: [`training_corpus/`](https://huggingface.co/beza4588/TenaOS/tree/main/training_corpus), [`adapter/training_metadata.json`](https://huggingface.co/beza4588/TenaOS/blob/main/adapter/training_metadata.json) |

## Scope Boundaries

| Area | Public framing |
| --- | --- |
| LoRA | The completed release claim is a trained task-tagged adapter and reproducible SFT corpus; workflow-level performance claims require full-runtime evaluation. |
| Audio and Amharic | The completed release claim is voice pathway support and Amharic text translation pathway support. |
| Clinical validation | The completed release claim is implementation evidence, corpus/index measurements, deterministic validation design, and internal technical evaluation. |
| CDS | The completed release claim is retrieval-grounded, cited, clinician-reviewed support, clinician-reviewed decision support. |
| Deployment | The completed release claim is a local single-container runtime and measured artifact footprint. |

## Headline Result Policy

The report leads with completed implementation evidence, measured infrastructure and corpus scale, and internal technical evaluation results.
