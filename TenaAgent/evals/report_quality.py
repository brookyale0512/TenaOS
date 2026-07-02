"""Quality scoring helpers for CIEL-backed report drafts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReportQualitySpec:
    id: str
    request: str
    expected_report_type: str | None = None
    required_concept_ids: list[str] = field(default_factory=list)
    required_group_by: list[str] = field(default_factory=list)
    requires_denominator: bool = False


def score_report(spec: ReportQualitySpec, draft: Any, ciel: Any | None = None) -> dict[str, Any]:
    payload = draft.to_dict() if hasattr(draft, "to_dict") else dict(draft or {})
    report_spec = payload.get("spec") or payload.get("reportSpec") or {}
    filters = list(report_spec.get("filters") or [])
    concept_ids = [str(f.get("conceptId") or "") for f in filters if f.get("conceptId")]
    group_by = [str(g.get("dimension") or "") for g in (report_spec.get("groupBy") or [])]
    missing_concepts = [cid for cid in spec.required_concept_ids if cid not in concept_ids]
    missing_groups = [dim for dim in spec.required_group_by if dim not in group_by]
    hallucinated_codes = _hallucinated_codes(concept_ids, ciel) if ciel is not None else []
    result = payload.get("lastResult") or payload.get("last_result")
    validation_ok = bool(payload.get("lastQuery") or payload.get("last_query") or result)
    score = 1.0
    if spec.expected_report_type and report_spec.get("reportType") != spec.expected_report_type:
        score -= 0.25
    if missing_concepts:
        score -= min(0.5, 0.2 * len(missing_concepts))
    if missing_groups:
        score -= min(0.25, 0.15 * len(missing_groups))
    if spec.requires_denominator and not report_spec.get("denominator"):
        score -= 0.25
    if hallucinated_codes:
        score -= 0.3
    if not validation_ok:
        score -= 0.2
    return {
        "id": spec.id,
        "score": max(0.0, round(score, 3)),
        "reportType": report_spec.get("reportType"),
        "missingConceptIds": missing_concepts,
        "missingGroupBy": missing_groups,
        "hallucinatedCodes": hallucinated_codes,
        "hasDenominator": bool(report_spec.get("denominator")),
        "validationOk": validation_ok,
    }


def quality_gate(scores: list[dict[str, Any]], *, min_average: float = 0.8) -> dict[str, Any]:
    if not scores:
        return {"pass": False, "average": 0.0, "reasons": ["No scores supplied."]}
    average = sum(float(s.get("score") or 0.0) for s in scores) / len(scores)
    reasons: list[str] = []
    if average < min_average:
        reasons.append(f"Average score {average:.2f} below required {min_average:.2f}.")
    for score in scores:
        if score.get("hallucinatedCodes"):
            reasons.append(f"{score.get('id')}: hallucinated CIEL codes {score.get('hallucinatedCodes')}.")
        if score.get("missingConceptIds"):
            reasons.append(f"{score.get('id')}: missing concepts {score.get('missingConceptIds')}.")
    return {"pass": not reasons, "average": round(average, 3), "reasons": reasons}


def _hallucinated_codes(concept_ids: list[str], ciel: Any) -> list[str]:
    missing: list[str] = []
    for cid in concept_ids:
        try:
            ciel.get_concept_bundle(cid)
        except Exception:
            missing.append(cid)
    return missing


__all__ = ["ReportQualitySpec", "score_report", "quality_gate"]
