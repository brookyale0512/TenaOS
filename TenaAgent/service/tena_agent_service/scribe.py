"""Text Scribe — Gemma 4 converts free-text clinical notes to SOAP + CIEL concepts + observations.

Pipeline:
  1. Clinician types unstructured clinical phrases
  2. build_scribe_prompt() wraps them for Gemma 4
  3. Gemma returns JSON: {soap, concepts, observations}
  4. parse_scribe_response() extracts and validates the result
  5. Backend resolves concept UUIDs from CIEL (vitals map first, then search)
  6. Confirmed items are saved: coded obs for diagnoses, valued obs for measurements
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

log = logging.getLogger("tenaos.tena_agent.scribe")

# ---------------------------------------------------------------------------
# Well-known CIEL vital/lab concept IDs — fast resolution without a search
# Key = normalised search terms (lowercased fragments)
# Value = CIEL concept numeric ID
# ---------------------------------------------------------------------------

KNOWN_CIEL_VITALS: dict[str, str] = {
    # Vitals
    "systolic": "5085",
    "systolic blood pressure": "5085",
    "sbp": "5085",
    "diastolic": "5086",
    "diastolic blood pressure": "5086",
    "dbp": "5086",
    "pulse": "5087",
    "heart rate": "5087",
    "hr": "5087",
    "temperature": "5088",
    "temp": "5088",
    "respiratory rate": "5242",
    "rr": "5242",
    "oxygen saturation": "5092",
    "spo2": "5092",
    "o2 sat": "5092",
    "oxygen sat": "5092",
    "weight": "5089",
    "weight kg": "5089",
    "height": "5090",
    "height cm": "5090",
    # Common labs
    "blood glucose": "887",
    "glucose": "887",
    "serum glucose": "887",
    "haemoglobin": "21",
    "hemoglobin": "21",
    "hb": "21",
    "hgb": "21",
    "white blood cells": "678",
    "wbc": "678",
    "cd4": "5497",
    "cd4 count": "5497",
    "viral load": "856",
    "creatinine": "791",
    "sodium": "1132",
    "potassium": "1133",
    "bicarbonate": "1135",
    # Other common obs
    "muac": "1622",
    "mid upper arm circumference": "1622",
}


def resolve_ciel_from_hint(hint: str) -> str | None:
    """Return CIEL concept ID for a hint string using the built-in vitals map."""
    h = hint.lower().strip()
    # Exact match first
    if h in KNOWN_CIEL_VITALS:
        return KNOWN_CIEL_VITALS[h]
    # Substring match — find the longest key that is a substring of hint
    best: str | None = None
    best_len = 0
    for key, ciel_id in KNOWN_CIEL_VITALS.items():
        if key in h and len(key) > best_len:
            best = ciel_id
            best_len = len(key)
    return best


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a clinical scribe assistant for a low-resource clinic. \
Convert an unstructured clinical note into a structured SOAP note. \
Also extract THREE separate lists: diagnoses, objective measurements, and prescribed medications.

SOAP FORMAT:
- Subjective: what the patient reports (symptoms, complaints, history). Write as a full sentence, e.g. "Patient reports headache for 2 days.", never as a bare fragment like "headache".
- Objective: measurable findings (vitals, exam, labs) — include all numeric values
- Assessment: clinical interpretation, diagnosis, or impression
- Plan: management, treatments, follow-up, investigations ordered — include ALL drugs prescribed

EXTRACTION — three arrays:

1. "concepts" — diagnoses, conditions, clinical findings (no numeric values):
   [{"label": "Hypertension", "ciel_hint": "hypertension"}, ...]

2. "observations" — objective measurements with numeric values (vitals, labs):
   [{"label": "Systolic Blood Pressure", "ciel_hint": "systolic blood pressure", "value": "170", "unit": "mmHg"}, ...]
   IMPORTANT: Split compound values (e.g. "BP 170/100" → two entries: systolic 170 + diastolic 100).
   Examples: pulse 88 bpm, SpO2 96 %, Hb 10.2 g/dL, weight 62 kg, glucose 5.4 mmol/L

3. "medications" — drugs/treatments prescribed in the PLAN section:
   [{"label": "Amlodipine", "ciel_hint": "amlodipine", "dose": "5mg", "frequency": "once daily", "route": "oral"}, ...]
   - "label": generic drug name only (e.g. "Amlodipine", not "Start Amlodipine 5mg daily")
   - "ciel_hint": lowercase generic name (e.g. "amlodipine")
   - "dose": dose with unit if stated (e.g. "5mg", "500mg", "0.1mg/kg")
   - "frequency": how often (e.g. "once daily", "twice daily", "q6h", "stat")
   - "route": route of administration if stated (e.g. "oral", "IV", "IM") — omit if not stated

STRICT RULES — READ CAREFULLY:
- EXTRACT ONLY what is EXPLICITLY STATED in the note/audio. Nothing else.
- The patient context is provided for background ONLY — do NOT copy values from it into the output.
- If someone says "temperature 36", extract ONLY temperature 36. No other observations.
- If the note is short (e.g. one measurement), ALL other arrays must be empty [].
- Do NOT guess, infer, or complete missing information from patient history.
- Do NOT add diagnoses, observations, or medications that were not spoken/written.
- If a SOAP section has nothing relevant, write "Not documented".
- Return ONLY valid JSON, no markdown fences, no extra text.

OUTPUT (return exactly this structure):
{
  "soap": {
    "subjective": "...",
    "objective": "...",
    "assessment": "...",
    "plan": "..."
  },
  "concepts": [
    {"label": "...", "ciel_hint": "..."}
  ],
  "observations": [
    {"label": "...", "ciel_hint": "...", "value": "...", "unit": "..."}
  ],
  "medications": [
    {"label": "...", "ciel_hint": "...", "dose": "...", "frequency": "...", "route": "..."}
  ]
}"""


def build_translation_prompt(amharic_text: str) -> list[dict[str, str]]:
    """Translate Amharic clinical note to English before scribing."""
    return [
        {
            "role": "system",
            "content": (
                "You are a medical translator. Translate the following Amharic clinical note "
                "to English accurately. Return ONLY the English translation — no explanations, "
                "no notes, no extra text. Preserve all clinical terms, measurements, and drug names."
            ),
        },
        {
            "role": "user",
            "content": f"Translate to English:\n\"\"\"\n{amharic_text.strip()}\n\"\"\"",
        },
    ]


def build_scribe_prompt(
    note_text: str,
    patient_summary: str | None = None,
) -> list[dict[str, str]]:
    """Build Gemma 4 chat messages for SOAP + concept + observation extraction."""
    user_content = ""
    if patient_summary:
        user_content += (
            f"[BACKGROUND — DO NOT EXTRACT FROM THIS — FOR CONTEXT ONLY]:\n"
            f"{patient_summary}\n\n"
        )
    user_content += (
        f"[NOTE TO SCRIBE — EXTRACT ONLY FROM THIS]:\n\"\"\"\n{note_text.strip()}\n\"\"\"\n\n"
        "Extract ONLY what is explicitly stated in the NOTE above. "
        "If the note is short, return short output. Return JSON only."
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

def parse_scribe_response(raw: str) -> dict[str, Any]:
    """Parse Gemma output into {soap, concepts, observations}."""
    # Strip thinking blocks
    text = re.sub(r"<think[\s\S]*?</think>", "", raw, flags=re.IGNORECASE).strip()
    text = re.sub(r"<thinking[\s\S]*?</thinking>", "", text, flags=re.IGNORECASE).strip()

    # Extract from code fences
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence_match:
        text = fence_match.group(1).strip()

    # Find the outermost JSON object
    obj_match = re.search(r"\{[\s\S]*\}", text)
    if not obj_match:
        log.warning("No JSON object found in scribe response")
        return _empty_result()

    json_str = obj_match.group(0)
    json_str = re.sub(r",\s*([}\]])", r"\1", json_str)  # remove trailing commas

    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError as exc:
        log.warning("Scribe JSON parse failed: %s", exc)
        return _empty_result()

    soap = parsed.get("soap") or {}
    soap_result = {
        "subjective": format_subjective_sentence(str(soap.get("subjective") or "Not documented").strip()),
        "objective": str(soap.get("objective") or "Not documented").strip(),
        "assessment": str(soap.get("assessment") or "Not documented").strip(),
        "plan": str(soap.get("plan") or "Not documented").strip(),
    }

    concepts: list[dict[str, str]] = []
    for item in (parsed.get("concepts") or []):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        hint = str(item.get("ciel_hint") or label).strip().lower()
        if label:
            concepts.append({"label": label, "ciel_hint": hint})

    observations: list[dict[str, str]] = []
    for item in (parsed.get("observations") or []):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        hint = str(item.get("ciel_hint") or label).strip().lower()
        value = str(item.get("value") or "").strip()
        unit = str(item.get("unit") or "").strip()
        if label and value:
            observations.append({"label": label, "ciel_hint": hint, "value": value, "unit": unit})
    observations = _expand_compound_observations(observations)

    medications: list[dict[str, str]] = []
    for item in (parsed.get("medications") or []):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        hint = str(item.get("ciel_hint") or label).strip().lower()
        dose = str(item.get("dose") or "").strip()
        frequency = str(item.get("frequency") or "").strip()
        route = str(item.get("route") or "").strip()
        if label:
            medications.append({
                "label": label,
                "ciel_hint": hint,
                "dose": dose,
                "frequency": frequency,
                "route": route,
            })

    return {
        "soap": soap_result,
        "concepts": concepts,
        "observations": observations,
        "medications": medications,
    }


def soap_to_note_text(soap: dict[str, str]) -> str:
    """Flatten SOAP dict into a clinical note string for OpenMRS storage."""
    lines: list[str] = []
    labels = {"subjective": "S", "objective": "O", "assessment": "A", "plan": "P"}
    for key, short in labels.items():
        val = (soap.get(key) or "Not documented").strip()
        if val and val.lower() != "not documented":
            lines.append(f"{short}: {val}")
    return "\n".join(lines) if lines else ""


def format_subjective_sentence(value: str) -> str:
    """Keep Subjective clinician-readable even when the model emits a fragment."""
    text = (value or "Not documented").strip()
    if not text or text.lower() == "not documented":
        return "Not documented"
    if text[-1] not in ".!?":
        text = f"{text}."
    if re.match(r"^(patient|pt|the patient|he|she|they)\b", text, flags=re.IGNORECASE):
        return text[0].upper() + text[1:]
    return f"Patient reports {text[0].lower()}{text[1:]}"


def _expand_compound_observations(items: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    bp_re = re.compile(r"^\s*(\d{2,3})\s*/\s*(\d{2,3})\s*$")
    for item in items:
        hint_label = re.sub(
            r"\s+",
            " ",
            re.sub(r"[^a-z0-9]+", " ", f"{item.get('label', '')} {item.get('ciel_hint', '')}".lower()),
        ).strip()
        match = bp_re.match(str(item.get("value") or ""))
        if match and ("blood pressure" in hint_label or hint_label == "bp"):
            systolic, diastolic = match.groups()
            out.append({
                "label": "Systolic blood pressure",
                "ciel_hint": "systolic blood pressure",
                "value": systolic,
                "unit": "mmHg",
            })
            out.append({
                "label": "Diastolic blood pressure",
                "ciel_hint": "diastolic blood pressure",
                "value": diastolic,
                "unit": "mmHg",
            })
            continue
        out.append(item)
    return out


def _empty_result() -> dict[str, Any]:
    return {
        "soap": {
            "subjective": "Not documented",
            "objective": "Not documented",
            "assessment": "Not documented",
            "plan": "Not documented",
        },
        "concepts": [],
        "observations": [],
        "medications": [],
    }


__all__ = [
    "build_scribe_prompt",
    "parse_scribe_response",
    "format_subjective_sentence",
    "resolve_ciel_from_hint",
    "soap_to_note_text",
    "KNOWN_CIEL_VITALS",
]
