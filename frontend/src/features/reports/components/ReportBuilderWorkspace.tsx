import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Play, Send, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/common/ErrorState";
import { cn } from "@/lib/utils";
import { useCdsHealth } from "@/features/forms/hooks/useFormBuilder";
import {
  useApplyReportOperations,
  useCreateReportDraft,
  useReportDraft,
  useReportDraftEvents,
  usePublishReport,
  useReportResult,
  useRunReport,
  useSendReportMessage,
} from "../hooks/useReportBuilder";
import { ReportBuilderChat } from "./ReportBuilderChat";
import { ReportBuilderPreview } from "./ReportBuilderPreview";
import { ReportSpecPanel } from "./ReportSpecPanel";
import type { ReportSpec, ValidationReport } from "../types/reportBuilder";

export function ReportBuilderWorkspace() {
  const params = useParams<{ draftId?: string }>();
  const navigate = useNavigate();
  const routeDraftId = params.draftId ?? null;

  const cdsHealth = useCdsHealth();
  const createDraft = useCreateReportDraft();
  const [draftId, setDraftId] = useState<string | null>(routeDraftId);

  const cdsServiceReady = cdsHealth.data?.ok === true;
  const cielReady = cdsHealth.data?.ciel?.available === true;
  const gemmaReady = cdsHealth.data?.vllm?.healthy === true;

  useEffect(() => {
    if (routeDraftId) { setDraftId(routeDraftId); return; }
    if (draftId) return;
    if (!cdsServiceReady) return;
    if (!cielReady) return;
    if (!gemmaReady) return;
    if (createDraft.isPending || createDraft.isError) return;
    createDraft.mutate({}, {
      onSuccess: (draft) => {
        setDraftId(draft.draftId);
        navigate(`/reports/${draft.draftId}`, { replace: true });
      },
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeDraftId, draftId, cdsServiceReady, cielReady, gemmaReady]);

  return (
    <div className="flex h-full max-h-full min-h-0 w-full overflow-hidden">
      {!draftId ? (
        <div className="p-4 md:p-6 space-y-3">
          {cdsHealth.isError && (
            <ErrorState
              title="CDS service is offline"
              description="The report builder requires the CDS service, Gemma 4, and CIEL to be reachable."
              onRetry={() => cdsHealth.refetch()}
            />
          )}
          <div className="mx-auto max-w-2xl py-16">
            <div className="rounded-3xl border bg-white p-6 space-y-3">
              <Skeleton className="h-5 w-40 mx-auto" />
              <Skeleton className="h-3 w-72 mx-auto" />
              <Skeleton className="h-32 w-full" />
            </div>
          </div>
        </div>
      ) : (
        <ActiveDraft draftId={draftId} />
      )}
    </div>
  );
}

function ActiveDraft({ draftId }: { draftId: string }) {
  const navigate = useNavigate();
  const draft = useReportDraft(draftId);
  const { events, status: sseStatus } = useReportDraftEvents(draftId);
  const sendMessage = useSendReportMessage(draftId);
  const applyOps = useApplyReportOperations(draftId);
  const runReport = useRunReport(draftId);
  const publishReport = usePublishReport(draftId);
  const resultQuery = useReportResult(draftId);
  const [serverValidation, setServerValidation] = useState<ValidationReport | null>(null);

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

  const onDragEnd = useCallback(() => { dragging.current = false; }, []);

  const spec = draft.data?.spec;
  const clientValidation = useMemo(() => validateReportSpec(spec), [spec]);
  const validation = useMemo(
    () => mergeValidation(clientValidation, serverValidation),
    [clientValidation, serverValidation],
  );
  const hasErrors = validation.issues.some((issue) => issue.severity === "error");
  const hasFilters = (spec?.filters?.length ?? 0) > 0;

  const handleApplyOperations = useCallback(
    (operations: Parameters<typeof applyOps.mutate>[0]) => {
      applyOps.mutate(operations, {
        onSuccess: (response) => setServerValidation(extractValidation(response)),
      });
    },
    [applyOps],
  );

  const handleRunReport = useCallback(() => {
    runReport.mutate(undefined, {
      onSuccess: (response) => setServerValidation(extractValidation(response)),
    });
  }, [runReport]);

  const chat = (
    <ReportBuilderChat
      events={events}
      sseStatus={sseStatus}
      isSending={sendMessage.isPending}
      onSend={(message) => sendMessage.mutate(message)}
    />
  );

  if (draft.isError) {
    return <ErrorState title="Could not load report" onRetry={() => draft.refetch()} />;
  }

  return (
    <div
      ref={containerRef}
      onPointerMove={onDragMove}
      onPointerUp={onDragEnd}
      className={cn(
        "flex min-h-0 flex-1 flex-col overflow-hidden overscroll-none lg:flex-row",
        "h-full",
      )}
    >
      {/* Left: title + actions + scrollable content */}
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden min-w-0">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 border-b shrink-0">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-semibold text-[var(--clinic-ink)] truncate">
                {draft.data?.name ?? "Untitled report"}
              </h1>
              <Badge variant="secondary">
                {draft.data?.status ?? "draft"}
              </Badge>
            </div>
            <p className="text-sm text-[hsl(var(--muted-foreground))]">
              {hasFilters ? `${spec?.filters.length} filter(s)` : "No filters yet"}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2 shrink-0">
            <Button variant="destructive" onClick={() => navigate("/reports/manage")}>
              <ArrowLeft size={14} className="mr-1.5 shrink-0" /> Cancel
            </Button>
            <Button
              disabled={!hasFilters || hasErrors || runReport.isPending}
              onClick={handleRunReport}
            >
              <Play size={14} className="mr-1.5" />
              {runReport.isPending ? "Running…" : "Run report"}
            </Button>
            <Button
              className="bg-[hsl(var(--primary))] text-white hover:opacity-90"
              disabled={publishReport.isPending || !draft.data?.lastResult}
              onClick={() => publishReport.mutate(!draft.data?.published)}
            >
              {draft.data?.published ? <X size={14} className="mr-1.5" /> : <Send size={14} className="mr-1.5" />}
              {draft.data?.published ? "Unpublish" : "Publish"}
            </Button>
          </div>
        </div>

        {/* Scrollable content */}
        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden p-4">
          <div className="min-h-0 flex-1 overflow-hidden">
            <ReportBuilderPreview
              validation={validation}
              result={resultQuery.data?.result ?? draft.data?.lastResult ?? null}
              status={resultQuery.data?.status ?? draft.data?.status}
              lastRunAt={resultQuery.data?.lastRunAt ?? draft.data?.lastRunAt}
              isRunning={runReport.isPending || draft.data?.status === "running"}
            />
          </div>
          <div className="min-h-0 max-h-[40%] shrink-0 overflow-hidden">
            {spec ? (
              <ReportSpecPanel
                spec={spec}
                disabled={applyOps.isPending}
                onOperation={handleApplyOperations}
              />
            ) : null}
          </div>
        </div>
      </div>

      {/* Drag handle */}
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
  );
}

function validateReportSpec(spec: ReportSpec | null | undefined): ValidationReport {
  const issues: ValidationReport["issues"] = [];
  if (!spec) return { issues };

  if (spec.reportType === "indicator" && !spec.denominator) {
    issues.push({
      severity: "error",
      path: "denominator",
      message: "Indicator reports require a denominator.",
    });
  }

  if (spec.reportType === "pivot" && spec.groupBy.length === 0) {
    issues.push({
      severity: "error",
      path: "groupBy",
      message: "Pivot reports require at least one grouping dimension.",
    });
  }

  return { issues };
}

function mergeValidation(
  clientValidation: ValidationReport,
  serverValidation: ValidationReport | null,
): ValidationReport {
  if (!serverValidation?.issues?.length) return clientValidation;
  const seen = new Set(clientValidation.issues.map((issue) => `${issue.severity}:${issue.path}:${issue.message}`));
  const issues = [...clientValidation.issues];
  for (const issue of serverValidation.issues) {
    const key = `${issue.severity}:${issue.path}:${issue.message}`;
    if (!seen.has(key)) issues.push(issue);
  }
  return { issues };
}

function extractValidation(response: unknown): ValidationReport | null {
  if (!response || typeof response !== "object") return null;
  const payload = response as {
    validation?: ValidationReport;
    build?: { validation?: ValidationReport };
    run?: { validation?: ValidationReport };
  };
  return payload.validation ?? payload.build?.validation ?? payload.run?.validation ?? null;
}
