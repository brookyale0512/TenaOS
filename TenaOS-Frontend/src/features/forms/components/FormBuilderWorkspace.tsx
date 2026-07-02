import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ArrowLeft, Rocket, AlertTriangle, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/common/ErrorState";
import { cn } from "@/lib/utils";
import { ConceptBasketDisplay } from "./ConceptBasketDisplay";
import { FormBuilderChat } from "./FormBuilderChat";
import { FormBuilderPreview } from "./FormBuilderPreview";
import {
  useApplyAction,
  useApplyOperations,
  useTenaAgentHealth,
  useCreateDraft,
  useDraft,
  useDraftEvents,
  useDraftSchema,
  usePublishDraft,
  useSendDraftMessage,
} from "../hooks/useFormBuilder";
import { useFormSchema } from "../hooks/useForms";
import type { BasketOperation, ConversationAction, ConversationState } from "../types/formBuilder";
import type { FormSchema } from "@/types/forms";

/**
 * Conversational form builder workspace.
 *
 * - Auto-creates a draft on first mount (no form-basics card). The
 *   conversation collects the name and encounter type itself.
 * - Stage 1 (awaiting_name, awaiting_encounter_type): centered chat card.
 * - Stage 2 (everything after): split pane — preview on the left taking
 *   roughly two-thirds; publish actions sit just above the assistant, which
 *   uses the remaining viewport height on large screens. The transition is
 *   a flat CSS shift; no library.
 */
interface WorkspaceHealth {
  tenaAgentReady: boolean;
  cielReady: boolean;
  gemmaReady: boolean;
  tenaAgentStale: boolean;
  isPending: boolean;
  isError: boolean;
  refetch: () => void;
}

export function FormBuilderWorkspace() {
  const [searchParams] = useSearchParams();
  const tenaAgentHealth = useTenaAgentHealth();
  const createDraft = useCreateDraft();
  const [draftId, setDraftId] = useState<string | null>(null);

  const tenaAgentReady = tenaAgentHealth.isSuccess && !!tenaAgentHealth.data;
  const cielReady = tenaAgentHealth.data?.ciel?.available === true;
  const gemmaReady = tenaAgentHealth.data?.llm?.healthy === true;
  const tenaAgentStale = tenaAgentHealth.isSuccess && !tenaAgentHealth.data?.ciel;

  // Only treat the agent as "offline" when we have never reached it. Once we
  // have a healthy snapshot, transient probe errors (a busy GPU, a proxy blip)
  // keep the last good data via placeholderData and must not flip to offline.
  const offline = tenaAgentHealth.isError && !tenaAgentHealth.data;

  const health: WorkspaceHealth = {
    tenaAgentReady,
    cielReady,
    gemmaReady,
    tenaAgentStale,
    isPending: tenaAgentHealth.isPending && !tenaAgentHealth.data,
    isError: offline,
    refetch: () => tenaAgentHealth.refetch(),
  };

  // formUuid is set when the user clicks "Edit" on a published form.
  const formUuid = searchParams.get("formUuid") ?? undefined;
  const existingSchema = useFormSchema(formUuid);

  // Seed payload from URL params (populated when editing an existing form).
  const seedPayload = {
    ...(searchParams.get("name") ? { name: searchParams.get("name")! } : {}),
    ...(searchParams.get("description") ? { description: searchParams.get("description")! } : {}),
    ...(searchParams.get("version") ? { version: searchParams.get("version")! } : {}),
    ...(searchParams.get("encounterTypeUuid") ? { encounterTypeUuid: searchParams.get("encounterTypeUuid")! } : {}),
  };

  // When editing, wait for the existing schema before creating the draft so
  // the basket can be seeded with all current questions.
  const schemaReady = !formUuid || existingSchema.isSuccess || existingSchema.isError;

  // Auto-create the draft once health is confirmed and both Gemma + CIEL are ready.
  useEffect(() => {
    if (draftId) return;
    if (!cielReady) return;
    if (!gemmaReady) return;
    if (!schemaReady) return;
    if (createDraft.isPending || createDraft.isError) return;
    const payload = {
      ...seedPayload,
      ...(existingSchema.data ? { importFormSchema: existingSchema.data } : {}),
    };
    createDraft.mutate(payload, {
      onSuccess: (draft) => setDraftId(draft.draftId),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftId, cielReady, gemmaReady, schemaReady]);

  return (
    <div className="flex h-full max-h-full min-h-0 w-full overflow-hidden">
      {!draftId ? (
        <DraftBootstrapping health={health} />
      ) : (
        <ActiveDraft draftId={draftId} health={health} initialPreviewSchema={existingSchema.data} />
      )}
    </div>
  );
}

function DraftBootstrapping({ health }: { health: WorkspaceHealth }) {
  return (
    <div className="space-y-3 p-4 md:p-6">
      <HealthErrors health={health} />
      <div className="mx-auto max-w-2xl py-16">
        <div className="rounded-3xl border bg-white p-6 space-y-3">
          <Skeleton className="h-5 w-40 mx-auto" />
          <Skeleton className="h-3 w-72 mx-auto" />
          <Skeleton className="h-32 w-full" />
        </div>
      </div>
    </div>
  );
}

function HealthErrors({ health }: { health: WorkspaceHealth }) {
  return (
    <>
      {health.tenaAgentStale && (
        <ErrorState
          title="TenaAgent service is running an older build"
          description="The TenaAgent service does not expose the form-builder endpoints. Restart the TenaAgent container."
        />
      )}
      {!health.tenaAgentReady && !health.isPending && !health.isError && (
        <ErrorState
          title="TenaAgent service is not reachable"
          description="The form builder requires the TenaAgent service at /agent-api to be running."
        />
      )}
      {health.isError && (
        <ErrorState
          title="TenaAgent service is offline"
          description="The form builder requires the TenaAgent service, Gemma 4, and CIEL to be reachable."
          onRetry={health.refetch}
        />
      )}
      {health.tenaAgentReady && !health.cielReady && !health.tenaAgentStale && (
        <ErrorState
          title="CIEL terminology is not reachable"
          description="The TenaAgent service cannot reach the CIEL search store. The form builder needs CIEL to pick concepts."
        />
      )}
      {health.tenaAgentReady && health.cielReady && !health.gemmaReady && (
        <ErrorState
          title="Gemma 4 is offline"
          description="Conversational form creation requires Gemma 4. Restart the model gateway before creating forms."
          onRetry={health.refetch}
        />
      )}
    </>
  );
}

interface ActiveDraftProps {
  draftId: string;
  health: WorkspaceHealth;
  initialPreviewSchema?: FormSchema;
}

function ActiveDraft({ draftId, health, initialPreviewSchema }: ActiveDraftProps) {
  const navigate = useNavigate();
  const draft = useDraft(draftId);
  const schema = useDraftSchema(draftId);
  const { events, status: sseStatus } = useDraftEvents(draftId);
  const sendMessage = useSendDraftMessage(draftId);
  const applyAction = useApplyAction(draftId);
  const applyOps = useApplyOperations(draftId);
  const publish = usePublishDraft(draftId);
  const [confirming, setConfirming] = useState(false);
  const [finalName, setFinalName] = useState("");
  const [chatWidth, setChatWidth] = useState(525);
  const containerRef = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);

  const onDragStart = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    dragging.current = true;
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  }, []);

  const onDragMove = useCallback((e: React.PointerEvent) => {
    if (!dragging.current || !containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const newWidth = rect.right - e.clientX;
    setChatWidth(Math.min(Math.max(newWidth, 280), rect.width - 400));
  }, []);

  const onDragEnd = useCallback(() => {
    dragging.current = false;
  }, []);

  const conversationState: ConversationState = (draft.data?.conversationState ?? "awaiting_name") as ConversationState;
  const validationIssues = (schema.data?.validation as { issues?: Array<{ severity: string }> } | undefined)?.issues ?? [];
  const hasErrors = validationIssues.some((issue) => issue.severity === "error");
  const isPublished = draft.data?.status === "published";
  const publishedUuid = draft.data?.publishedFormUuid;
  const previewSchema = schema.data?.schema ?? initialPreviewSchema;

  const sectionCount = draft.data?.basket?.sections?.length ?? 0;
  const fieldCount = useMemo(
    () => (draft.data?.basket?.sections ?? []).reduce((total, section) => total + section.fields.length, 0),
    [draft.data?.basket?.sections],
  );

  const onSendMessage = (message: string) => sendMessage.mutate(message);
  const onAction = (action: ConversationAction) => applyAction.mutate(action);
  const onApplyOperations = (operations: BasketOperation[]) => applyOps.mutate(operations);

  const startPublish = () => {
    setFinalName(draft.data?.name ?? "");
    setConfirming(true);
  };

  const confirmPublish = async () => {
    const payload = finalName.trim() && finalName.trim() !== draft.data?.name
      ? { name: finalName.trim(), markPublished: true }
      : { markPublished: true };
    const result = await publish.mutateAsync(payload);
    if (result?.success) setConfirming(false);
  };

  if (draft.isError) {
    return <ErrorState title="Could not load draft" onRetry={() => draft.refetch()} />;
  }

  const chat = (
    <FormBuilderChat
      events={events}
      conversationState={conversationState}
      sseStatus={sseStatus}
      isSending={sendMessage.isPending}
      isApplyingAction={applyAction.isPending}
      onSend={onSendMessage}
      onAction={onAction}
    />
  );

  // Always render the two-panel workspace regardless of conversation state.
  // All title/publish controls live inside the left panel so the right chat
  // panel can span the full height of the container with nothing above it.
  return (
    <>
    <div
      ref={containerRef}
      onPointerMove={onDragMove}
      onPointerUp={onDragEnd}
      className={cn(
        "flex min-h-0 flex-1 flex-col overflow-hidden overscroll-none lg:flex-row",
        "h-full",
      )}
    >
      {/* Left: form title + actions + scrollable preview */}
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden min-w-0">
        {/* Top bar: form name + cancel + publish */}
        <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 border-b shrink-0">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-semibold text-[var(--clinic-ink)] truncate">
                {draft.data?.name ?? "Untitled form"}
              </h1>
              <Badge variant={isPublished ? "success" : draft.data?.status === "failed" ? "destructive" : "secondary"}>
                {draft.data?.status ?? "draft"}
              </Badge>
            </div>
            <p className="text-sm text-[hsl(var(--muted-foreground))]">
              {sectionCount} section{sectionCount === 1 ? "" : "s"} · {fieldCount} question{fieldCount === 1 ? "" : "s"}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2 shrink-0">
            <Button variant="destructive" onClick={() => navigate("/forms")}>
              <ArrowLeft size={14} className="mr-1.5 shrink-0" /> Cancel
            </Button>
            <Button
              onClick={startPublish}
              disabled={hasErrors || isPublished || publish.isPending || !draft.data?.lastSchema || fieldCount === 0}
            >
              <Rocket size={14} className="mr-1.5 shrink-0" /> Publish
            </Button>
            {isPublished && publishedUuid && (
              <Button variant="secondary" size="sm" onClick={() => navigate(`/forms/${publishedUuid}/fill`)}>
                Open published form
              </Button>
            )}
          </div>
        </div>
        <div className="shrink-0">
          <HealthErrors health={health} />
        </div>
        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden p-4">
          <div className="min-h-0 flex-1 overflow-hidden">
            <FormBuilderPreview
              schema={previewSchema}
              validation={schema.data?.validation as never}
            />
          </div>
          <div className="min-h-0 max-h-[35%] shrink-0 overflow-hidden">
            <ConceptBasketDisplay
              basket={draft.data?.basket}
              disabled={isPublished || applyOps.isPending}
              onOperation={onApplyOperations}
            />
          </div>
        </div>
      </div>

      {/* Drag handle — visually 1px line, but 6px hit area for easy grabbing */}
      <div
        onPointerDown={onDragStart}
        className="hidden lg:flex w-1.5 shrink-0 cursor-col-resize items-center justify-center group"
        title="Drag to resize"
      >
        <div className="w-px h-full bg-[var(--clinic-border)] group-hover:bg-[var(--clinic-slate)]/50 transition-colors" />
      </div>

      {/* Right: full-height chat panel */}
      <div
        className="shrink-0 border-t lg:border-t-0 flex flex-col h-80 lg:h-full lg:min-h-0"
        style={{ width: `${chatWidth}px` }}
      >
        {chat}
      </div>
    </div>

    <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Publish form to OpenMRS</DialogTitle>
            <DialogDescription>
              Publishing creates a real OpenMRS Form and attaches the deterministic schema. Encounters can record this form once published.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div className="space-y-1.5">
              <Label htmlFor="publish-name">Final form name</Label>
              <Input
                id="publish-name"
                value={finalName}
                onChange={(event) => setFinalName(event.target.value)}
                placeholder={draft.data?.name}
              />
            </div>
            {hasErrors && (
              <div className="rounded-xl border border-[hsl(var(--destructive))] bg-[hsl(var(--destructive))]/10 p-3 text-sm text-[hsl(var(--destructive))]">
                <div className="flex items-center gap-1.5 font-semibold">
                  <AlertTriangle size={14} /> Cannot publish
                </div>
                <p className="text-xs mt-1">The current schema has validation errors. Resolve them in the preview before publishing.</p>
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="secondary" onClick={() => setConfirming(false)} disabled={publish.isPending}>
              Cancel
            </Button>
            <Button onClick={confirmPublish} disabled={publish.isPending || hasErrors}>
              {publish.isPending ? "Publishing…" : (
                <>
                  <Send size={14} className="mr-1.5" /> Confirm publish
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
