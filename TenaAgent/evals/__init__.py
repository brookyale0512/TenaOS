"""Form-quality evaluation harness for the grounded v2 form builder.

Modules:
  * ``form_quality`` — deterministic scorer + dataset loader + a live-model CLI
    that drives the real ``FormConversationDriver`` and applies a quality gate
    (concept coverage, datatype correctness, zero hallucinated/invalid codes,
    no retired concepts, valid schema).

The scorer is import-light and side-effect free so it can be reused from pytest
(see ``tests/test_form_pipeline_e2e.py``) as well as from the live CLI.
"""
