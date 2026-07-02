"""Conversational state machine for the form builder.

Owns the five-state conversation lifecycle:
  awaiting_name → awaiting_encounter_type → awaiting_question
  (+ awaiting_candidate_pick / awaiting_set_decision for set-member flows)

Every user turn flows through handle_user_turn. State transitions and basket
mutations happen here; Gemma model calls are delegated to form_agent_runner.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from .ciel import CielClient, ConceptNotFoundError
from .config import Settings
from .form_agent_runner import (
    build_deterministic_summary,
    compact_tool_result,  # re-exported for tests
    run_gemma_tool_agent,
)

_compact_tool_result = compact_tool_result  # underscore alias for test imports
from .form_builder_tool_loop import FORM_OPENAI_TOOLS, FormBuilderToolLoop  # noqa: F401 (FORM_OPENAI_TOOLS used by tests)
from .form_drafts import ConversationState, DraftNotFoundError, FormDraft, FormDraftStore
from .llm_client import LlmClient


TurnKind = Literal["message", "action"]


@dataclass(frozen=True)
class ConversationTurn:
    kind: TurnKind
    message: str | None = None
    action: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Operation name constants (single source of truth for frontend + tests)

OP_AGENT_PROMPT = "agent_prompt"
OP_USER_MESSAGE = "user_message"
OP_USER_ACTION = "user_action"
OP_CANDIDATE_PICKER = "candidate_picker"
OP_SET_DECISION = "set_decision"
OP_ENCOUNTER_TYPE_PICKER = "encounter_type_picker"
OP_FIELD_ADDED = "field_added"
OP_FIELDS_ADDED = "fields_added"
OP_DUPLICATE_REJECTED = "duplicate_rejected"
OP_CONCEPT_ERROR = "concept_error"
OP_NAME_SET = "name_set"
OP_ENCOUNTER_TYPE_SET = "encounter_type_set"
OP_FORM_PLAN_CREATED = "form_plan_created"
OP_FORM_PLAN_APPLIED = "form_plan_applied"
OP_FORM_EDIT_APPLIED = "form_edit_applied"
OP_AGENT_REASONING = "agent_reasoning"
OP_MODEL_TOOL_CALL = "model_tool_call"
OP_TOOL_RESULT = "tool_result"
OP_CIEL_REVIEW = "ciel_review"

_PROMPT_NAME = "Let's create a new form."
_PROMPT_ENCOUNTER_TYPE = (
    "What type of clinical encounter does this form record? "
    "Pick from the list — encounters submitted through this form will use that type."
)
_PROMPT_FIRST_QUESTION = "Great. What should be the first question on the form? Describe it in your own words."
_PROMPT_NEXT_QUESTION = "What should be the next question?"
DEFAULT_FORM_ENCOUNTER_TYPE_DISPLAY = "Adult Visit"


class FormConversationDriver:
    """Owns the conversation state machine for a single form draft."""

    def __init__(
        self,
        *,
        store: FormDraftStore,
        ciel: CielClient,
        loop: FormBuilderToolLoop,
        llm: LlmClient | None,
        settings: Settings | None = None,
    ) -> None:
        self.store = store
        self.ciel = ciel
        self.loop = loop
        self.llm = llm
        self.settings = settings or Settings.from_env()

    # ----------------------------------------------------------------- driver

    def handle_user_turn(self, draft_id: str, turn: ConversationTurn) -> None:
        try:
            draft = self.store.get_draft(draft_id)
        except DraftNotFoundError:
            raise

        self._log_input(draft_id, turn)

        state = draft.conversation_state
        try:
            if state == "awaiting_name":
                self._handle_awaiting_name(draft, turn)
            elif state == "awaiting_encounter_type":
                self._handle_awaiting_encounter_type(draft, turn)
            elif state == "awaiting_question":
                self._handle_awaiting_question(draft, turn)
            elif state == "awaiting_candidate_pick":
                self._handle_awaiting_candidate_pick(draft, turn)
            elif state == "awaiting_set_decision":
                self._handle_awaiting_set_decision(draft, turn)
            elif state in {"publishing", "published"}:
                self._emit_text(
                    draft_id,
                    "This draft has already been finalised. Open the published form to fill it.",
                )
            else:
                self._emit_text(draft_id, f"Unknown conversation state: {state}")
        except Exception as exc:
            self.store.append_event(
                draft_id,
                actor="middleware",
                operation="conversation_error",
                detail=f"{type(exc).__name__}: {exc}",
                payload={"state": state, "turn": _turn_to_dict(turn)},
            )
            raise

    def kickoff(self, draft_id: str) -> None:
        self._emit_prompt(draft_id, _PROMPT_NAME)

    # ------------------------------------------------------------- state: name

    def _handle_awaiting_name(self, draft: FormDraft, turn: ConversationTurn) -> None:
        if turn.kind != "message" or not (turn.message or "").strip():
            self._emit_prompt(draft.draft_id, _PROMPT_NAME)
            return
        message = (turn.message or "").strip()
        name = _form_name_from_request(message)
        context = dict(draft.conversation_context or {})
        context["pendingFormRequest"] = message
        self.store.update_draft(draft.draft_id, name=name, conversation_context=context)
        self.store.append_event(
            draft.draft_id,
            actor="user",
            operation=OP_NAME_SET,
            detail=f"Form name set to '{name}'",
            payload={"name": name},
        )
        if draft.encounter_type_uuid:
            self.store.update_draft(
                draft.draft_id,
                conversation_state="awaiting_question",
                conversation_context={},
            )
            self.store.append_event(
                draft.draft_id,
                actor="system",
                operation=OP_ENCOUNTER_TYPE_SET,
                detail=f"Encounter type defaulted to '{DEFAULT_FORM_ENCOUNTER_TYPE_DISPLAY}'",
                payload={"encounterTypeUuid": draft.encounter_type_uuid, "display": DEFAULT_FORM_ENCOUNTER_TYPE_DISPLAY},
            )
            self._run_agent(self.store.get_draft(draft.draft_id), message, mode="create")
            return
        self._emit_encounter_type_picker(draft.draft_id)
        self.store.update_draft(draft.draft_id, conversation_state="awaiting_encounter_type")

    # --------------------------------------------------- state: encounter type

    def _handle_awaiting_encounter_type(self, draft: FormDraft, turn: ConversationTurn) -> None:
        encounter_uuid: str | None = None
        encounter_label: str | None = None

        if turn.kind == "action" and turn.action == "pick_encounter_type":
            encounter_uuid = str(turn.payload.get("encounterTypeUuid") or "").strip() or None
            encounter_label = str(turn.payload.get("display") or "").strip() or None
        elif turn.kind == "message":
            requested = (turn.message or "").strip().lower()
            cached = draft.conversation_context.get("encounterTypeOptions") or []
            for option in cached:
                if option.get("display", "").lower() == requested:
                    encounter_uuid = option.get("uuid")
                    encounter_label = option.get("display")
                    break

        if not encounter_uuid:
            self._emit_text(draft.draft_id, "Please pick one of the encounter types above to continue.")
            self._emit_encounter_type_picker(draft.draft_id)
            return

        pending_form_request = str((draft.conversation_context or {}).get("pendingFormRequest") or "").strip()
        self.store.update_draft(
            draft.draft_id,
            encounter_type_uuid=encounter_uuid,
            conversation_state="awaiting_question",
            conversation_context={},
        )
        self.store.append_event(
            draft.draft_id,
            actor="user",
            operation=OP_ENCOUNTER_TYPE_SET,
            detail=f"Encounter type set to '{encounter_label or encounter_uuid}'",
            payload={"encounterTypeUuid": encounter_uuid, "display": encounter_label},
        )
        if pending_form_request:
            latest = self.store.get_draft(draft.draft_id)
            self._run_agent(latest, pending_form_request, mode="create")
            return
        self._emit_prompt(draft.draft_id, _PROMPT_FIRST_QUESTION)

    # ------------------------------------------------------ state: question

    def _handle_awaiting_question(self, draft: FormDraft, turn: ConversationTurn) -> None:
        if turn.kind != "message" or not (turn.message or "").strip():
            self._emit_prompt(draft.draft_id, _PROMPT_NEXT_QUESTION)
            return
        description = (turn.message or "").strip()
        if not self._llm_healthy():
            self._emit_text(
                draft.draft_id,
                "I need Gemma 4 online to create or edit forms in this workspace. "
                "The model is unavailable right now; please try again when the assistant is online.",
            )
            return
        mode: Literal["create", "edit"] = "edit" if _basket_field_count(draft) > 0 else "create"
        self._run_agent(draft, description, mode=mode)

    # ------------------------------------------------ state: candidate pick

    def _handle_awaiting_candidate_pick(self, draft: FormDraft, turn: ConversationTurn) -> None:
        if turn.kind == "message":
            self.store.update_draft(draft.draft_id, conversation_state="awaiting_question")
            self._handle_awaiting_question(draft, turn)
            return
        if turn.action != "pick_candidate":
            self._emit_text(draft.draft_id, "Pick one of the candidates above, or describe the question differently.")
            return
        concept_id = str(turn.payload.get("conceptId") or "").strip()
        if not concept_id:
            self._emit_text(draft.draft_id, "Missing conceptId in the candidate click.")
            return
        try:
            bundle = self.ciel.get_concept_bundle(concept_id)
        except ConceptNotFoundError:
            self._emit_text(draft.draft_id, f"Concept {concept_id} is not in the CIEL store.")
            return
        concept = bundle.get("concept", {}) or {}
        if concept.get("retired"):
            self.store.append_event(
                draft.draft_id,
                actor="middleware",
                operation=OP_CONCEPT_ERROR,
                detail=f"Concept {concept_id} is retired and cannot be added.",
                payload={"conceptId": concept_id},
            )
            self._emit_prompt(draft.draft_id, _PROMPT_NEXT_QUESTION)
            self.store.update_draft(draft.draft_id, conversation_state="awaiting_question")
            return

        set_members = bundle.get("set_members") or []
        if set_members:
            self._enter_set_decision(draft, concept_id, concept, set_members)
            return

        display_label = concept.get("display_name") or concept_id
        ok = self._add_field(draft, concept_id, display_label)
        if not ok:
            self._emit_text(
                draft.draft_id,
                f"I couldn't bring '{display_label}' (CIEL {concept_id}) into your OpenMRS instance — "
                "the concept registration failed. Pick another candidate above, or describe the question differently.",
            )
            self.store.update_draft(draft.draft_id, conversation_state="awaiting_question", conversation_context={})
            return
        self.store.update_draft(draft.draft_id, conversation_state="awaiting_question", conversation_context={})
        self._emit_prompt(draft.draft_id, _PROMPT_NEXT_QUESTION)

    # ------------------------------------------------ state: set decision

    def _handle_awaiting_set_decision(self, draft: FormDraft, turn: ConversationTurn) -> None:
        if turn.kind != "action" or turn.action != "set_decision":
            self._emit_text(draft.draft_id, "Pick 'Add all' or 'Pick specific' to continue.")
            return
        choice = str(turn.payload.get("choice") or "").strip()
        ctx = draft.conversation_context or {}
        seed_concept_id = ctx.get("seedConceptId")
        seed_label = ctx.get("seedDisplayName") or seed_concept_id
        members = ctx.get("members") or []

        if not seed_concept_id or not members:
            self._emit_text(draft.draft_id, "Set context was lost. Please describe the question again.")
            self.store.update_draft(draft.draft_id, conversation_state="awaiting_question", conversation_context={})
            self._emit_prompt(draft.draft_id, _PROMPT_NEXT_QUESTION)
            return

        if choice == "add_all":
            added = self._add_members(draft, members)
            self.store.append_event(
                draft.draft_id,
                actor="user",
                operation=OP_FIELDS_ADDED,
                detail=f"Added {len(added)} field(s) from set '{seed_label}'.",
                payload={"seedConceptId": seed_concept_id, "added": added},
            )
            self.store.update_draft(draft.draft_id, conversation_state="awaiting_question", conversation_context={})
            self._emit_prompt(draft.draft_id, _PROMPT_NEXT_QUESTION)
        elif choice == "pick_specific":
            self.store.update_draft(
                draft.draft_id,
                conversation_state="awaiting_candidate_pick",
                conversation_context={
                    "description": f"members of {seed_label}",
                    "query": str(seed_label),
                    "candidates": [
                        {
                            "conceptId": str(member.get("conceptId")),
                            "displayName": member.get("displayName") or str(member.get("conceptId")),
                            "datatype": member.get("datatype"),
                            "conceptClass": member.get("conceptClass"),
                            "rationale": [f"member of set '{seed_label}'"],
                            "answerCount": int(member.get("answerCount", 0) or 0),
                            "setMemberCount": int(member.get("setMemberCount", 0) or 0),
                        }
                        for member in members[:8]
                    ],
                },
            )
            self.store.append_event(
                draft.draft_id,
                actor="middleware",
                operation=OP_CANDIDATE_PICKER,
                detail=f"Pick a member of '{seed_label}' to add.",
                payload={
                    "prompt": f"Pick a member of '{seed_label}' to add.",
                    "candidates": [
                        {
                            "conceptId": str(member.get("conceptId")),
                            "displayName": member.get("displayName") or str(member.get("conceptId")),
                            "datatype": member.get("datatype"),
                            "conceptClass": member.get("conceptClass"),
                            "rationale": [f"member of '{seed_label}'"],
                        }
                        for member in members[:8]
                    ],
                    "originalDescription": f"members of {seed_label}",
                },
            )
        else:
            self._emit_text(draft.draft_id, "Pick 'Add all' or 'Pick specific'.")

    # ----------------------------------------------------- helpers: set entry

    def _enter_set_decision(
        self,
        draft: FormDraft,
        seed_concept_id: str,
        seed_concept: dict[str, Any],
        raw_set_members: list[dict[str, Any]],
    ) -> None:
        members: list[dict[str, Any]] = []
        for rel in raw_set_members:
            target = rel.get("target") or {}
            target_id = target.get("concept_id")
            if not target_id:
                continue
            members.append({
                "conceptId": str(target_id),
                "displayName": target.get("display_name") or str(target_id),
                "datatype": target.get("datatype"),
                "conceptClass": target.get("concept_class"),
                "answerCount": int(target.get("answer_count", 0) or 0),
                "setMemberCount": int(target.get("set_member_count", 0) or 0),
            })
        seed_label = seed_concept.get("display_name") or seed_concept_id
        self.store.update_draft(
            draft.draft_id,
            conversation_state="awaiting_set_decision",
            conversation_context={"seedConceptId": seed_concept_id, "seedDisplayName": seed_label, "members": members},
        )
        self.store.append_event(
            draft.draft_id,
            actor="middleware",
            operation=OP_SET_DECISION,
            detail=f"'{seed_label}' is a set with {len(members)} member(s). Add all, or pick specific members?",
            payload={
                "seed": {"conceptId": seed_concept_id, "displayName": seed_label, "conceptClass": seed_concept.get("concept_class")},
                "memberCount": len(members),
                "memberPreview": [m.get("displayName") for m in members[:6]],
                "members": members,
            },
        )

    # ----------------------------------------- helpers: basket mutation

    def _add_field(self, draft: FormDraft, concept_id: str, display_name: str) -> bool:
        try:
            bundle = self.ciel.get_concept_bundle(concept_id)
        except ConceptNotFoundError:
            self.store.append_event(
                draft.draft_id,
                actor="middleware",
                operation=OP_CONCEPT_ERROR,
                detail=f"Concept {concept_id} is not in the CIEL store.",
                payload={"conceptId": concept_id},
            )
            return False
        try:
            writer = self.loop.writer_factory()
            seeded, missing_answer_names = writer.ensure_concept_with_answers(bundle)
        except Exception as exc:
            seeded, missing_answer_names = False, [str(exc)]
        if not seeded:
            self.store.append_event(
                draft.draft_id,
                actor="middleware",
                operation=OP_CONCEPT_ERROR,
                detail=f"Could not seed concept '{display_name}' (CIEL {concept_id}) into OpenMRS. Missing/failed: {missing_answer_names}.",
                payload={"conceptId": concept_id, "missing": missing_answer_names},
            )
            return False
        if missing_answer_names:
            self.store.append_event(
                draft.draft_id,
                actor="middleware",
                operation="answer_seeding_partial",
                detail=f"Field '{display_name}' added but {len(missing_answer_names)} answer code(s) could not be seeded in OpenMRS.",
                payload={"conceptId": concept_id, "missingAnswers": missing_answer_names},
            )

        draft = self.store.get_draft(draft.draft_id)
        basket = draft.basket or {"sections": []}
        sections = basket.get("sections") or []
        if not sections:
            sections = [{"sectionId": "questions", "label": draft.name or "Questions", "fields": [], "conceptId": None, "kind": "container", "isExpanded": True}]
            basket = {"sections": sections}
            self.store.update_draft(draft.draft_id, basket=basket)
            draft = self.store.get_draft(draft.draft_id)

        target_section_id = sections[0]["sectionId"]
        result = self.loop.update_form_draft(
            draft.draft_id,
            [{"op": "add_field", "sectionId": target_section_id, "conceptId": concept_id}],
            actor="user",
        )
        warnings = result.get("warnings", [])
        if warnings:
            reason = warnings[0].get("reason", "Could not add the concept.")
            if "already in section" in reason.lower():
                self.store.append_event(
                    draft.draft_id,
                    actor="middleware",
                    operation=OP_DUPLICATE_REJECTED,
                    detail=f"'{display_name}' is already in the form.",
                    payload={"conceptId": concept_id},
                )
            else:
                self.store.append_event(
                    draft.draft_id,
                    actor="middleware",
                    operation=OP_CONCEPT_ERROR,
                    detail=reason,
                    payload={"conceptId": concept_id},
                )
            return False
        self.store.append_event(
            draft.draft_id,
            actor="user",
            operation=OP_FIELD_ADDED,
            detail=f"Added field '{display_name}'.",
            payload={"conceptId": concept_id, "displayName": display_name, "sectionId": target_section_id},
        )
        self.loop.build_form_schema(draft.draft_id)
        return True

    def _add_members(self, draft: FormDraft, members: list[dict[str, Any]]) -> list[dict[str, Any]]:
        added: list[dict[str, Any]] = []
        for member in members:
            concept_id = str(member.get("conceptId") or "").strip()
            if not concept_id:
                continue
            try:
                bundle = self.ciel.get_concept_bundle(concept_id)
            except ConceptNotFoundError:
                continue
            concept = bundle.get("concept", {}) or {}
            if concept.get("retired") or bundle.get("set_members"):
                continue
            display = concept.get("display_name") or member.get("displayName") or concept_id
            ok = self._add_field(draft, concept_id, display)
            if ok:
                added.append({"conceptId": concept_id, "displayName": display})
        return added

    # --------------------------------------------- agent delegation

    def _run_agent(self, draft: FormDraft, request: str, *, mode: Literal["create", "edit"]) -> None:
        if not self._llm_healthy():
            self._emit_text(
                draft.draft_id,
                "I need Gemma 4 online to create or edit forms in this workspace. The model is unavailable right now.",
            )
            return
        if getattr(self.settings, "form_agent_pipeline_v2", False):
            # v2 grounded pipeline: research -> CIEL resolution -> repair.
            from .form_pipeline import run_form_pipeline_agent

            run_form_pipeline_agent(
                store=self.store,
                loop=self.loop,
                llm=self.llm,  # type: ignore[arg-type]
                draft=draft,
                request=request,
                mode=mode,
                settings=self.settings,
            )
            return
        run_gemma_tool_agent(
            store=self.store,
            loop=self.loop,
            llm=self.llm,  # type: ignore[arg-type]
            draft=draft,
            request=request,
            mode=mode,
            settings=self.settings,
        )

    # -------------------------------------------- helpers: emit events

    def _emit_prompt(self, draft_id: str, text: str) -> None:
        self.store.append_event(draft_id, actor="gemma", operation=OP_AGENT_PROMPT, detail=text, payload={"text": text})

    def _emit_text(self, draft_id: str, text: str) -> None:
        self._emit_prompt(draft_id, text)

    def _emit_encounter_type_picker(self, draft_id: str) -> None:
        encounter_types = self._fetch_encounter_types(draft_id)
        try:
            context = dict(self.store.get_draft(draft_id).conversation_context or {})
        except DraftNotFoundError:
            context = {}
        context["encounterTypeOptions"] = encounter_types
        self.store.update_draft(draft_id, conversation_context=context)
        self.store.append_event(
            draft_id,
            actor="middleware",
            operation=OP_ENCOUNTER_TYPE_PICKER,
            detail=_PROMPT_ENCOUNTER_TYPE,
            payload={"prompt": _PROMPT_ENCOUNTER_TYPE, "encounterTypes": encounter_types},
        )

    def _fetch_encounter_types(self, draft_id: str) -> list[dict[str, Any]]:
        try:
            writer = self.loop.writer_factory()
            return writer.list_encounter_types(limit=100)
        except Exception as exc:
            self.store.append_event(
                draft_id,
                actor="middleware",
                operation="encounter_type_fetch_failed",
                detail=f"Could not list encounter types: {exc}",
                payload={"error": str(exc)},
            )
            return []

    def _log_input(self, draft_id: str, turn: ConversationTurn) -> None:
        if turn.kind == "message":
            self.store.append_event(
                draft_id,
                actor="user",
                operation=OP_USER_MESSAGE,
                detail=(turn.message or "").strip(),
                payload={"message": (turn.message or "").strip()},
            )
        else:
            self.store.append_event(
                draft_id,
                actor="user",
                operation=OP_USER_ACTION,
                detail=f"User action: {turn.action}",
                payload={"action": turn.action, "payload": turn.payload},
            )

    def _llm_healthy(self) -> bool:
        if self.llm is None:
            return False
        try:
            return bool(self.llm.health().healthy)
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Module-level helpers


def _basket_field_count(draft: FormDraft) -> int:
    return sum(len(s.get("fields") or []) for s in (draft.basket.get("sections") or []))


def _form_name_from_request(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip(" .,!?:;'\"")
    match = re.search(
        r"\b(?:build|create|make|draft|generate)\s+(?:me\s+|us\s+|a\s+|an\s+|the\s+)*(.+?\bform)\b",
        cleaned,
        re.IGNORECASE,
    )
    if match:
        candidate = match.group(1)
    else:
        candidate = cleaned
    candidate = re.sub(r"\b(?:we|i|need|want|to|please|for|the|a|an|me|us)\b", " ", candidate, flags=re.IGNORECASE)
    candidate = re.sub(r"\s+", " ", candidate).strip(" .,!?:;'\"")
    return candidate[:1].upper() + candidate[1:] if candidate else "Untitled form"


def _turn_to_dict(turn: ConversationTurn) -> dict[str, Any]:
    return {"kind": turn.kind, "message": turn.message, "action": turn.action, "payload": turn.payload}


# Re-export for backwards compatibility (tests import directly from this module).
__all__ = [
    "ConversationTurn",
    "FormConversationDriver",
    "OP_AGENT_PROMPT",
    "OP_AGENT_REASONING",
    "OP_CANDIDATE_PICKER",
    "OP_CIEL_REVIEW",
    "OP_CONCEPT_ERROR",
    "OP_DUPLICATE_REJECTED",
    "OP_ENCOUNTER_TYPE_PICKER",
    "OP_ENCOUNTER_TYPE_SET",
    "OP_FIELD_ADDED",
    "OP_FIELDS_ADDED",
    "OP_FORM_EDIT_APPLIED",
    "OP_FORM_PLAN_APPLIED",
    "OP_FORM_PLAN_CREATED",
    "OP_MODEL_TOOL_CALL",
    "OP_NAME_SET",
    "OP_SET_DECISION",
    "OP_TOOL_RESULT",
    "OP_USER_ACTION",
    "OP_USER_MESSAGE",
    "build_deterministic_summary",
    "compact_tool_result",
    "_compact_tool_result",
]
