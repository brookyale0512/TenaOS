// Types mirroring the TenaAgent service form_builder data shapes.
//
// These map 1:1 to the Python dataclasses in
// `TenaAgent/service/tena_agent_service/form_drafts.py` and
// `TenaAgent/service/tena_agent_service/form_builder.py`. Keep them in sync.

import type { FormSchema } from "@/types/forms";

export type DraftStatus =
  | "draft"
  | "publishing"
  | "published"
  | "failed"
  | "archived";

export type EventActor = "user" | "gemma" | "middleware" | "system";

export type ConversationState =
  | "awaiting_name"
  | "awaiting_encounter_type"
  | "awaiting_question"
  | "awaiting_candidate_pick"
  | "awaiting_set_decision"
  | "publishing"
  | "published";

/** Structured action payloads for POST /forms/drafts/{id}/actions. */
export type ConversationAction =
  | {
      action: "pick_encounter_type";
      payload: { encounterTypeUuid: string; display?: string };
    }
  | {
      action: "pick_candidate";
      payload: { conceptId: string };
    }
  | {
      action: "set_decision";
      payload: { choice: "add_all" | "pick_specific" };
    };

/** Candidate concept attached to a candidate_picker event. */
export interface ConversationCandidate {
  conceptId: string;
  displayName: string;
  datatype: string | null;
  conceptClass: string | null;
  answerCount?: number;
  setMemberCount?: number;
  rationale: string[];
}

export interface SetDecisionSeed {
  conceptId: string;
  displayName: string;
  conceptClass: string | null;
}

export interface EncounterTypeOption {
  uuid: string;
  display: string;
  name: string;
}

export interface BasketField {
  conceptId: string;
  labelOverride: string | null;
  required: boolean;
  renderingOverride: string | null;
}

export interface BasketSection {
  sectionId: string;
  label: string;
  fields: BasketField[];
  conceptId: string | null;
  kind: "section_concept" | "container";
  isExpanded: boolean;
}

export interface ConceptBasket {
  sections: BasketSection[];
}

export interface ValidationIssue {
  severity: "error" | "warning";
  path: string;
  message: string;
}

export interface ValidationReport {
  issues: ValidationIssue[];
}

export interface FormDraft {
  draftId: string;
  owner: string | null;
  status: DraftStatus;
  name: string;
  version: string;
  description: string | null;
  encounterTypeUuid: string | null;
  basket: ConceptBasket;
  lastSchema: FormSchema | null;
  lastValidation: ValidationReport | null;
  publishedFormUuid: string | null;
  createdAt: string;
  updatedAt: string;
  conversationState: ConversationState;
  conversationContext: Record<string, unknown>;
}

export interface FormDraftEvent {
  eventId: string;
  draftId: string;
  timestamp: string;
  actor: EventActor;
  operation: string;
  detail: string;
  payload: Record<string, unknown>;
}

export interface ModelToolCallPayload {
  phase?: string;
  temperature?: number;
  toolName?: string;
  arguments?: Record<string, unknown>;
  step?: number;
}

export interface ToolResultPayload {
  toolName?: string;
  result?: Record<string, unknown>;
  step?: number;
}

export interface AgentReasoningPayload {
  phase?: string;
  temperature?: number;
  text?: string;
  mode?: string;
}

export interface CielReviewPayload {
  toolName?: string;
  arguments?: Record<string, unknown>;
  result?: Record<string, unknown>;
  step?: number;
}

export interface PublishStep {
  name: string;
  status: string;
  detail: string;
  payload: Record<string, unknown>;
}

export interface PublishResult {
  formUuid: string | null;
  success: boolean;
  steps: PublishStep[];
  error: string | null;
}

/** Structured basket-mutation operations the user can apply directly. */
export type BasketOperation =
  | { op: "add_section"; sectionId?: string; label?: string; conceptId?: string }
  | { op: "remove_section"; sectionId: string }
  | { op: "rename_section"; sectionId: string; label: string }
  | {
      op: "add_field";
      sectionId: string;
      conceptId: string;
      label?: string;
      required?: boolean;
    }
  | { op: "remove_field"; sectionId: string; conceptId: string }
  | {
      op: "set_required";
      sectionId: string;
      conceptId: string;
      required: boolean;
    }
  | { op: "set_label"; sectionId: string; conceptId: string; label: string }
  | { op: "reorder_sections"; sectionIds: string[] }
  | { op: "reorder_fields"; sectionId: string; conceptIds: string[] };
