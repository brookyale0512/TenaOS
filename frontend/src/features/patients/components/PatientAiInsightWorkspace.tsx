import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  Bot,
  Brain,
  CheckCircle2,
  ChevronDown,
  ClipboardList,
  Globe,
  Loader2,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Stethoscope,
  Wrench,
} from "lucide-react";
import { Workspace } from "@/components/workspace";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { KbHit, PatientInsightTrace, StructuredCds } from "../hooks/usePatientAiInsight";
import { useTranslateCds } from "../hooks/useTranslateCds";

interface PatientAiInsightWorkspaceProps {
  open: boolean;
  onClose: () => void;
  patientName: string;
  trace?: PatientInsightTrace;
  isLoading: boolean;
  onRun: () => void;
}

export function PatientAiInsightWorkspace({
  open,
  onClose,
  patientName,
  trace,
  isLoading,
  onRun,
}: PatientAiInsightWorkspaceProps) {
  const cds = trace?.structuredCds;
  const translation = useTranslateCds();

  return (
    <Workspace
      open={open}
      onClose={onClose}
      title="AI Insight"
      subtitle={`WHO/MSF evidence-grounded CDS — ${patientName}`}
      wide
      headerAction={
        cds?.content ? (
          translation.isAmharic ? (
            <Button
              type="button"
              className="inline-flex items-center gap-1.5 rounded-xl bg-[hsl(var(--primary))] px-4 py-2 text-sm font-medium text-white hover:opacity-90"
              onClick={translation.backToEnglish}
            >
              <ArrowLeft className="size-4" />
              Back to English
            </Button>
          ) : (
            <Button
              type="button"
              className="inline-flex items-center gap-1.5 rounded-xl bg-[hsl(var(--primary))] px-4 py-2 text-sm font-medium text-white hover:opacity-90"
              disabled={translation.isPending}
              onClick={() => translation.translate(cds.summary, cds.content!)}
            >
              {translation.isPending ? <Loader2 className="size-4 animate-spin" /> : <Globe className="size-4" />}
              {translation.isPending ? "Translating..." : "Translate to Amharic"}
            </Button>
          )
        ) : null
      }
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
          <Button onClick={onRun} disabled={isLoading}>
            {isLoading ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : (
              <Bot className="mr-2 size-4" />
            )}
            {trace ? "Refresh" : "Get AI Insight"}
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        {/* Source badge */}
        <div className="flex items-center gap-2 rounded-xl border border-blue-100 bg-blue-50 px-4 py-2.5">
          <ShieldCheck className="size-4 shrink-0 text-blue-500" />
          <p className="text-sm text-blue-800">
            Gemma 4 searches <strong>58,000+ WHO/MSF guideline chunks</strong> and cites only retrieved evidence.
          </p>
        </div>

        {/* Loading indicator */}
        {(isLoading || trace?.status === "running") && (
          <div className="flex items-center gap-3 rounded-xl border border-[var(--clinic-border)] bg-white px-4 py-3 text-sm text-[hsl(var(--muted-foreground))]">
            <Loader2 className="size-4 animate-spin text-[var(--clinic-blue)]" />
              Gemma 4 E4B is reasoning and searching WHO/MSF guidelines…
          </div>
        )}

        {/* Trace panel */}
        <details open={!cds} className="group rounded-xl border border-[var(--clinic-border)] bg-white">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3">
            <span className="flex items-center gap-2 text-sm font-semibold text-[var(--clinic-ink)]">
              <ChevronDown className="size-4 shrink-0 transition-transform group-open:rotate-180 text-[hsl(var(--muted-foreground))]" />
              <Wrench className="size-4 shrink-0 text-[hsl(var(--muted-foreground))]" />
              How this CDS was generated
            </span>
            <Badge
              variant={trace?.status === "completed" ? "success" : "info"}
              className="shrink-0 text-[10px] uppercase"
            >
              {groupTraceEvents(trace?.events ?? []).length} steps
            </Badge>
          </summary>
          <div className="border-t border-[var(--clinic-border)] px-4 py-3 space-y-2">
            {trace?.events.length ? (
              groupTraceEvents(trace.events).map((group, i) => (
                <InsightTraceGroupRow key={i} group={group} />
              ))
            ) : (
              <p className="text-sm text-[hsl(var(--muted-foreground))]">
                Click <strong>Run Insight</strong> to see Gemma 4's reasoning, search
                queries, and KB results step by step.
              </p>
            )}
          </div>
        </details>

        {/* CDS result */}
        {cds && <StructuredCdsCard cds={cds} translation={translation} />}
      </div>
    </Workspace>
  );
}

// ── Trace grouping (mirrors PatientMaterialWorkspace) ─────────────────────

type TraceEvent = PatientInsightTrace["events"][number];

type InsightTraceGroup =
  | { kind: "tool"; call: TraceEvent; result: TraceEvent | null }
  | { kind: "reasoning"; event: TraceEvent }
  | { kind: "summary"; event: TraceEvent };

function groupTraceEvents(events: TraceEvent[]): InsightTraceGroup[] {
  const groups: InsightTraceGroup[] = [];
  let i = 0;
  while (i < events.length) {
    const ev = events[i];
    if (ev.type === "model_reasoning") {
      groups.push({ kind: "reasoning", event: ev });
      i++;
    } else if (ev.type === "model_summary") {
      groups.push({ kind: "summary", event: ev });
      i++;
    } else if (ev.type === "model_tool_call") {
      const next = events[i + 1];
      const result = next?.type === "middleware_result" ? next : null;
      groups.push({ kind: "tool", call: ev, result });
      i += result ? 2 : 1;
    } else {
      groups.push({ kind: "tool", call: ev, result: null });
      i++;
    }
  }
  return groups;
}

function InsightTraceGroupRow({ group }: { group: InsightTraceGroup }) {
  if (group.kind === "reasoning") {
    const ev = group.event;
    return (
      <details className="group rounded-lg border border-violet-100 bg-violet-50">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2">
          <span className="flex min-w-0 items-center gap-2">
            <ChevronDown className="size-3.5 shrink-0 text-violet-400 transition-transform group-open:rotate-180" />
            <Brain className="size-3.5 shrink-0 text-violet-500" />
            <span className="truncate text-xs font-semibold text-violet-800">{cleanReasoningTitle(ev.title)}</span>
          </span>
          <Badge variant="outline" className="shrink-0 text-[9px] uppercase border-violet-200 text-violet-600">reasoning</Badge>
        </summary>
        <div className="border-t border-violet-100 px-3 py-2.5">
          <p className="text-xs leading-relaxed text-violet-900 whitespace-pre-wrap">{ev.detail}</p>
        </div>
      </details>
    );
  }

  if (group.kind === "summary") {
    const ev = group.event;
    return (
      <div className="flex items-center gap-2 rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-2">
        <Sparkles className="size-3.5 shrink-0 text-emerald-500" />
        <span className="text-xs font-semibold text-emerald-800">{ev.title}</span>
        <Badge variant="success" className="ml-auto shrink-0 text-[9px] uppercase">done</Badge>
      </div>
    );
  }

  const { call, result } = group;
  const args = call.payload?.arguments as Record<string, unknown> | undefined;
  const query = (args?.query as string | undefined) ?? (call.payload?.query as string | undefined);
  const headerLabel = query
    ? `${call.title}: "${query.slice(0, 60)}${query.length > 60 ? "…" : ""}"`
    : call.title;
  const hitsReturned = result?.payload?.hits_returned as number | undefined;
  const topHit = result?.payload?.top_hit as string | undefined;

  return (
    <details className="group rounded-lg border border-[var(--clinic-border)] bg-white">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2">
        <span className="flex min-w-0 items-center gap-2">
          <ChevronDown className="size-3.5 shrink-0 text-[hsl(var(--muted-foreground))] transition-transform group-open:rotate-180" />
          <Wrench className="size-3.5 shrink-0 text-sky-500" />
          <span className="truncate text-xs font-semibold text-[var(--clinic-ink)]">{headerLabel}</span>
        </span>
        <div className="flex shrink-0 items-center gap-1.5">
          {hitsReturned !== undefined && (
            <span className="text-[9px] font-medium text-emerald-600">{hitsReturned} hits</span>
          )}
          <Badge variant="info" className="text-[9px] uppercase">tool call</Badge>
        </div>
      </summary>
      <div className="border-t border-[var(--clinic-border)] divide-y divide-[var(--clinic-border)]">
        {args && Object.keys(args).length > 0 && (
          <div className="px-3 py-2.5 space-y-1">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">Input</p>
            <pre className="text-[11px] leading-relaxed text-[var(--clinic-ink)] whitespace-pre-wrap break-all font-mono bg-[hsl(var(--muted))] rounded px-2 py-1.5 max-h-32 overflow-y-auto">
              {JSON.stringify(args, null, 2)}
            </pre>
          </div>
        )}
        {result && (
          <div className="px-3 py-2.5 space-y-1">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">Result</p>
            <p className="text-xs leading-relaxed text-[var(--clinic-ink)]">{result.detail}</p>
            {topHit && (
              <p className="text-[11px] text-[hsl(var(--muted-foreground))]">Top hit: <em>{topHit}</em></p>
            )}
          </div>
        )}
        {!args && !result && call.detail && (
          <div className="px-3 py-2.5">
            <p className="text-xs leading-relaxed text-[hsl(var(--muted-foreground))]">{call.detail}</p>
          </div>
        )}
      </div>
    </details>
  );
}

function cleanReasoningTitle(title: string): string {
  return title.replace(/\s*\(step\s+\d+\)\s*$/i, "");
}


// ── Section config ────────────────────────────────────────────────────────

const SECTION_CONFIG: Record<string, {
  icon: React.ReactNode;
  borderClass: string;
  bgClass: string;
  labelClass: string;
  textClass: string;
}> = {
  "Clinical Assessment": {
    icon: <Stethoscope className="size-4 text-blue-600" />,
    borderClass: "border-blue-200",
    bgClass: "bg-blue-50",
    labelClass: "text-blue-700",
    textClass: "text-blue-950",
  },
  "Evidence-Based Considerations": {
    icon: <Activity className="size-4 text-violet-600" />,
    borderClass: "border-violet-200",
    bgClass: "bg-violet-50",
    labelClass: "text-violet-700",
    textClass: "text-violet-950",
  },
  "Suggested Actions": {
    icon: <ClipboardList className="size-4 text-emerald-600" />,
    borderClass: "border-emerald-200",
    bgClass: "bg-emerald-50",
    labelClass: "text-emerald-700",
    textClass: "text-emerald-950",
  },
  "Safety Alerts": {
    icon: <ShieldAlert className="size-4 text-amber-600" />,
    borderClass: "border-amber-200",
    bgClass: "bg-amber-50",
    labelClass: "text-amber-700",
    textClass: "text-amber-950",
  },
  "Key Points": {
    icon: <Sparkles className="size-4 text-[var(--clinic-blue)]" />,
    borderClass: "border-[var(--clinic-border)]",
    bgClass: "bg-[hsl(var(--muted)/0.4)]",
    labelClass: "text-[var(--clinic-slate)]",
    textClass: "text-[var(--clinic-ink)]",
  },
};

function parseCdsSections(content: string): Array<{ title: string; body: string }> {
  const sections: Array<{ title: string; body: string }> = [];
  const re = /^## (.+)$/gm;
  const matches = [...content.matchAll(re)];
  for (let i = 0; i < matches.length; i++) {
    const title = matches[i][1].trim();
    const start = matches[i].index! + matches[i][0].length;
    const end = i + 1 < matches.length ? matches[i + 1].index! : content.length;
    const body = content.slice(start, end).trim();
    if (body) sections.push({ title, body });
  }
  return sections;
}

function renderMdBody(body: string, textClass: string): React.ReactNode {
  const lines = body.split("\n");
  const nodes: React.ReactNode[] = [];
  let listItems: string[] = [];
  let isOrdered = false;
  let listStart = 1;
  let key = 0;

  const flushList = () => {
    if (!listItems.length) return;
    if (isOrdered) {
      nodes.push(
        <ol key={key++} className="mt-2 space-y-1.5 pl-1">
          {listItems.map((item, i) => (
            <li key={i} className={`flex gap-2.5 text-sm leading-relaxed ${textClass}`}>
              <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-white/60 text-[10px] font-bold opacity-70">
                {listStart + i}
              </span>
              <span dangerouslySetInnerHTML={{ __html: renderInlineMd(item) }} />
            </li>
          ))}
        </ol>,
      );
    } else {
      nodes.push(
        <ul key={key++} className="mt-2 space-y-1.5">
          {listItems.map((item, i) => (
            <li key={i} className={`flex gap-2.5 text-sm leading-relaxed ${textClass}`}>
              <span className="mt-2 size-1.5 shrink-0 rounded-full bg-current opacity-50" />
              <span dangerouslySetInnerHTML={{ __html: renderInlineMd(item) }} />
            </li>
          ))}
        </ul>,
      );
    }
    listItems = [];
    isOrdered = false;
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      flushList();
      continue;
    }
    const numMatch = trimmed.match(/^(\d+)\.\s+(.*)/);
    const bulletMatch = trimmed.match(/^[-*•]\s+(.*)/);

    if (numMatch) {
      if (!listItems.length) { isOrdered = true; listStart = parseInt(numMatch[1]); }
      listItems.push(numMatch[2]);
    } else if (bulletMatch) {
      listItems.push(bulletMatch[1]);
    } else {
      flushList();
      nodes.push(
        <p
          key={key++}
          className={`mt-1.5 text-sm leading-relaxed ${textClass}`}
          dangerouslySetInnerHTML={{ __html: renderInlineMd(trimmed) }}
        />,
      );
    }
  }
  flushList();
  return <>{nodes}</>;
}

function renderInlineMd(text: string): string {
  return text
    // Compact citation tags: *(WHO: ...) or *(MSF: ...) — render as a source pill
    .replace(
      /\*\((WHO|MSF):\s*([^)]+)\)\*/g,
      '<span class="ml-1 inline-flex items-center rounded-full border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-blue-700">$1: $2</span>',
    )
    // Also handle trailing *(WHO Guidelines)* or *(MSF Clinical Guidelines)* without colon
    .replace(
      /\*\((WHO|MSF)[^)]*\)\*/g,
      '<span class="ml-1 inline-flex items-center rounded-full border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-blue-700">$1</span>',
    )
    // Longer-form citations: "(According to WHO Guidelines (KB evidence): ...)" → pill
    .replace(
      /\(According to ([^(]+?)\s*\(KB evidence\):\s*([^)]{0,80}[^)]*)\)/g,
      '<span class="ml-1 inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[9px] font-medium text-slate-600">$1</span>',
    )
    // *Not in KB* → muted badge
    .replace(
      /\*Not in KB\*/g,
      '<span class="ml-1 text-[10px] italic text-[hsl(var(--muted-foreground))]">Not in KB</span>',
    )
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+?)\*/g, "<em>$1</em>")
    .replace(/`(.+?)`/g, '<code class="rounded bg-black/10 px-1 py-0.5 text-[11px] font-mono">$1</code>');
}

interface TranslationState {
  isAmharic: boolean;
  amharicSummary: string | undefined;
  amharicContent: string | undefined;
  isPending: boolean;
  translate: (summary: string, content: string) => void;
  backToEnglish: () => void;
}

function StructuredCdsCard({ cds, translation }: { cds: StructuredCds; translation: TranslationState }) {
  const isRecommendation = cds.status === "recommendation";
  const StatusIcon = isRecommendation ? CheckCircle2 : AlertTriangle;

  const displayContent = translation.isAmharic && translation.amharicContent
    ? translation.amharicContent
    : cds.content;
  const displaySummary = translation.isAmharic && translation.amharicSummary
    ? translation.amharicSummary
    : cds.summary;
  const sections = displayContent ? parseCdsSections(displayContent) : [];

  return (
    <div className="space-y-4">
      {/* Recommendation header card — original colors preserved */}
      <div
        className={`rounded-xl border px-4 py-3 ${
          isRecommendation
            ? "border-emerald-200 bg-emerald-50"
            : "border-amber-200 bg-amber-50"
        }`}
      >
        <div className="flex items-start gap-3">
          <StatusIcon
            className={`mt-0.5 size-5 shrink-0 ${
              isRecommendation ? "text-emerald-600" : "text-amber-600"
            }`}
          />
          <div className="min-w-0 flex-1">
            <p
              className={`text-sm font-semibold leading-snug ${
                isRecommendation ? "text-emerald-900" : "text-amber-900"
              }`}
            >
              {displaySummary}
            </p>
            <div className="mt-1.5 flex flex-wrap items-center gap-2">
              <Badge
                variant={isRecommendation ? "success" : "outline"}
                className="text-[10px] uppercase"
              >
                {cds.status.replaceAll("_", " ")}
              </Badge>
              {translation.isAmharic && (
                <Badge variant="outline" className="text-[10px] uppercase border-violet-200 text-violet-700">
                  አማርኛ
                </Badge>
              )}
              {cds.streaming && (
                <Badge variant="info" className="gap-1 text-[10px] uppercase">
                  <Loader2 className="size-3 animate-spin" />
                  streaming
                </Badge>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* 5-section report */}
      {sections.length > 0 ? (
        sections.map(({ title, body }) => {
          const cfg = SECTION_CONFIG[title] ?? {
            icon: null,
            borderClass: "border-[var(--clinic-border)]",
            bgClass: "bg-[hsl(var(--muted)/0.3)]",
            labelClass: "text-[var(--clinic-slate)]",
            textClass: "text-[var(--clinic-ink)]",
          };
          return (
            <div
              key={title}
              className={`overflow-hidden rounded-2xl border-2 ${cfg.borderClass} bg-white shadow-sm`}
            >
              {/* Section header */}
              <div className={`flex items-center gap-2.5 px-5 py-3 ${cfg.bgClass} border-b-2 ${cfg.borderClass}`}>
                {cfg.icon}
                <span className={`text-[11px] font-bold uppercase tracking-widest ${cfg.labelClass}`}>
                  {title}
                </span>
              </div>
              {/* Section body */}
              <div className="px-5 py-4">
                {renderMdBody(body, cfg.textClass)}
              </div>
            </div>
          );
        })
        ) : cds.detail ? (
          <div className="rounded-xl border border-[var(--clinic-border)] bg-white px-4 py-3">
            <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase text-[hsl(var(--muted-foreground))]">
              <Sparkles className="size-3.5" />
              {translation.isAmharic ? "ጀምራ 4 ማጠቃለያ" : "Gemma 4 Summary"}
            </div>
            <p className="text-sm leading-relaxed text-[var(--clinic-ink)]">{cds.detail}</p>
          </div>
        ) : null}

      {/* Evidence sources */}
      {cds.kbHits?.length ? <EvidenceSources hits={cds.kbHits} /> : null}
    </div>
  );
}

// ── Evidence Sources ───────────────────────────────────────────────────────

function EvidenceSources({ hits }: { hits: KbHit[] }) {
  const visible = hits.slice(0, 3);
  const extra = hits.length - visible.length;

  return (
    <div>
      <div className="mb-2 flex items-center gap-1.5 text-xs font-bold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
        <BookOpen className="size-3.5" />
        WHO/MSF Evidence Sources
        <span className="rounded-full bg-[hsl(var(--muted))] px-1.5 py-0.5 text-[10px] font-semibold">
          {hits.length}
        </span>
      </div>
      <div className="space-y-2">
        {visible.map((hit, i) => (
          <EvidenceHit key={`${hit.title}-${i}`} hit={hit} rank={i + 1} />
        ))}
        {extra > 0 && (
          <details className="group">
            <summary className="flex cursor-pointer list-none items-center gap-1.5 rounded-lg border border-[var(--clinic-border)] px-3 py-2 text-xs font-medium text-[hsl(var(--muted-foreground))] hover:bg-[hsl(var(--muted)/0.4)] transition-colors">
              <ChevronDown className="size-3.5 transition-transform group-open:rotate-180" />
              Show {extra} more source{extra > 1 ? "s" : ""}
            </summary>
            <div className="mt-2 space-y-2">
              {hits.slice(3).map((hit, i) => (
                <EvidenceHit key={`${hit.title}-extra-${i}`} hit={hit} rank={i + 4} />
              ))}
            </div>
          </details>
        )}
      </div>
    </div>
  );
}

function EvidenceHit({ hit, rank }: { hit: KbHit; rank: number }) {
  const isMsf = hit.source?.toLowerCase().includes("msf");
  const scoreDisplay = (hit.score * 1000).toFixed(0);
  const isActionable =
    hit.content_type === "recommendation" || hit.content_type === "implementation";

  return (
    <details className="group overflow-hidden rounded-xl border border-[var(--clinic-border)] bg-white">
      <summary className="flex cursor-pointer list-none items-start gap-3 px-4 py-3">
        {/* Rank */}
        <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-[hsl(var(--muted))] text-[10px] font-bold text-[hsl(var(--muted-foreground))]">
          {rank}
        </span>
        {/* Title + badges */}
        <span className="min-w-0 flex-1">
          <span className="block text-xs font-semibold leading-snug text-[var(--clinic-ink)] group-open:text-clip">
            {hit.title}
          </span>
          <span className="mt-1 flex flex-wrap items-center gap-1.5">
            <Badge
              variant="outline"
              className={`text-[9px] font-semibold uppercase ${
                isMsf
                  ? "border-orange-200 bg-orange-50 text-orange-700"
                  : "border-blue-200 bg-blue-50 text-blue-700"
              }`}
            >
              {isMsf ? "MSF" : "WHO"}
            </Badge>
            {isActionable && (
              <Badge variant="success" className="text-[9px] uppercase">
                {hit.content_type}
              </Badge>
            )}
            {hit.recommendation_strength && (
              <Badge variant="outline" className="text-[9px] uppercase">
                {hit.recommendation_strength}
              </Badge>
            )}
            <span className="text-[10px] font-medium tabular-nums text-[hsl(var(--muted-foreground))]">
              Score {scoreDisplay}
            </span>
          </span>
        </span>
        <ChevronDown className="mt-1 size-4 shrink-0 text-[hsl(var(--muted-foreground))] transition-transform group-open:rotate-180" />
      </summary>
      <div className="border-t border-[var(--clinic-border)] bg-[hsl(var(--muted)/0.3)] px-4 py-3">
        <p className="whitespace-pre-wrap text-xs leading-relaxed text-[var(--clinic-ink)]">
          {hit.content}
        </p>
        {hit.evidence_certainty && (
          <p className="mt-2 text-[10px] font-medium text-[hsl(var(--muted-foreground))]">
            Certainty of evidence: {hit.evidence_certainty}
          </p>
        )}
      </div>
    </details>
  );
}
