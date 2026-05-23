import { useCallback, useEffect, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  Ban,
  BookOpen,
  Brain,
  CalendarClock,
  ChevronDown,
  Edit3,
  Globe,
  Heart,
  Loader2,
  Mail,
  Pill,
  Printer,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  Wrench,
} from "lucide-react";
import { Workspace } from "@/components/workspace";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { tenaAgentClient } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import { toast } from "@/stores/uiStore";
import type { PatientMaterialTrace } from "../hooks/usePatientMaterial";

interface PatientMaterialWorkspaceProps {
  open: boolean;
  onClose: () => void;
  patientName: string;
  trace?: PatientMaterialTrace;
  isLoading: boolean;
  onRun: () => void;
}

// ── Section config ────────────────────────────────────────────────────────

const SECTION_CONFIG: Record<string, {
  icon: React.ReactNode;
  borderClass: string;
  bgClass: string;
  labelClass: string;
  textClass: string;
}> = {
  "What You Have": {
    icon: <Heart className="size-4 text-teal-600" />,
    borderClass: "border-teal-200",
    bgClass: "bg-teal-50",
    labelClass: "text-teal-700",
    textClass: "text-teal-950",
  },
  "Why It Matters": {
    icon: <AlertCircle className="size-4 text-amber-600" />,
    borderClass: "border-amber-200",
    bgClass: "bg-amber-50",
    labelClass: "text-amber-700",
    textClass: "text-amber-950",
  },
  "What To Do": {
    icon: <Sparkles className="size-4 text-emerald-600" />,
    borderClass: "border-emerald-200",
    bgClass: "bg-emerald-50",
    labelClass: "text-emerald-700",
    textClass: "text-emerald-950",
  },
  "Your Medications": {
    icon: <Pill className="size-4 text-violet-600" />,
    borderClass: "border-violet-200",
    bgClass: "bg-violet-50",
    labelClass: "text-violet-700",
    textClass: "text-violet-950",
  },
  "What to Avoid": {
    icon: <Ban className="size-4 text-orange-600" />,
    borderClass: "border-orange-200",
    bgClass: "bg-orange-50",
    labelClass: "text-orange-700",
    textClass: "text-orange-950",
  },
  "Follow-Up Schedule": {
    icon: <CalendarClock className="size-4 text-blue-600" />,
    borderClass: "border-blue-200",
    bgClass: "bg-blue-50",
    labelClass: "text-blue-700",
    textClass: "text-blue-950",
  },
  "When To Seek Help": {
    icon: <ShieldAlert className="size-4 text-red-600" />,
    borderClass: "border-red-200",
    bgClass: "bg-red-50",
    labelClass: "text-red-700",
    textClass: "text-red-950",
  },
};

// ── Section parsing ───────────────────────────────────────────────────────

function parseSections(content: string): Array<{ title: string; body: string }> {
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

function sectionsToContent(sections: Array<{ title: string; body: string }>): string {
  return sections.map((s) => `## ${s.title}\n${s.body}`).join("\n\n");
}

function splitTranslatedMaterial(translated: string): { title?: string; content: string } {
  const clean = translated.trim();
  const titleMatch = clean.match(/^#\s+(.+?)\s*\n+/);
  if (titleMatch) {
    return {
      title: titleMatch[1].trim(),
      content: clean.slice(titleMatch[0].length).trim(),
    };
  }
  const firstSection = clean.search(/^##\s+/m);
  if (firstSection > 0) {
    const possibleTitle = clean.slice(0, firstSection).replace(/^#+\s*/, "").trim();
    return {
      title: possibleTitle || undefined,
      content: clean.slice(firstSection).trim(),
    };
  }
  return { content: clean };
}

// ── Main workspace ────────────────────────────────────────────────────────

export function PatientMaterialWorkspace({
  open,
  onClose,
  patientName,
  trace,
  isLoading,
  onRun,
}: PatientMaterialWorkspaceProps) {
  const material = trace?.material;
  const [editableSections, setEditableSections] = useState<Array<{ title: string; body: string }>>([]);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [isAmharic, setIsAmharic] = useState(false);
  const [amharicTitle, setAmharicTitle] = useState<string | undefined>();
  const [amharicContent, setAmharicContent] = useState<string | undefined>();
  const [isTranslating, setIsTranslating] = useState(false);

  // Sync editable sections when material arrives
  useEffect(() => {
    if (material?.content) {
      setEditableSections(parseSections(material.content));
      setIsAmharic(false);
      setAmharicTitle(undefined);
      setAmharicContent(undefined);
    }
  }, [material?.content]);

  const handleTranslate = useCallback(async () => {
    if (!material) return;
    if (amharicContent) { setIsAmharic(true); return; }
    setIsTranslating(true);
    try {
      const content = sectionsToContent(editableSections) || material.content;
      const { data } = await tenaAgentClient.post<{ translatedContent: string }>("/translate", {
        content: `# ${material.title}\n\n${content}`,
        language: "Amharic",
      });
      const translated = splitTranslatedMaterial(data.translatedContent);
      setAmharicTitle(translated.title);
      setAmharicContent(translated.content);
      setIsAmharic(true);
    } catch (err) {
      toast.error("Translation failed", describeError(err));
    } finally {
      setIsTranslating(false);
    }
  }, [material, amharicContent, editableSections]);

  const handlePrint = useCallback(() => {
    const content = isAmharic && amharicContent
      ? parseSections(amharicContent)
      : editableSections;
    const title = isAmharic && amharicTitle
      ? amharicTitle
      : material?.title ?? "Create Care Guide with AI";

    // Build print-friendly HTML — opens in a new window
    const sectionsHtml = content.map(({ title: sTitle, body }) => {
      const bodyHtml = body
        .split("\n")
        .map((line) => {
          const trimmed = line.trim();
          if (!trimmed) return "";
          const numMatch = trimmed.match(/^(\d+)\.\s+(.*)/);
          const bulletMatch = trimmed.match(/^[-*•]\s+(.*)/);
          if (numMatch) return `<li>${numMatch[2]}</li>`;
          if (bulletMatch) return `<li>${bulletMatch[1]}</li>`;
          return `<p>${trimmed}</p>`;
        })
        .join("");

      return `
        <div class="section">
          <h2>${sTitle}</h2>
          ${bodyHtml}
        </div>`;
    }).join("");

    const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>${title} — ${patientName}</title>
  <style>
    body { font-family: Arial, sans-serif; font-size: 12pt; max-width: 700px; margin: 0 auto; padding: 24px; color: #111; }
    h1 { font-size: 18pt; color: #0f766e; margin-bottom: 4px; }
    .subtitle { font-size: 10pt; color: #555; margin-bottom: 20px; }
    .section { border: 1px solid #ddd; border-radius: 8px; margin-bottom: 16px; overflow: hidden; }
    h2 { font-size: 12pt; font-weight: bold; background: #f3f4f6; padding: 8px 12px; margin: 0; border-bottom: 1px solid #ddd; }
    p { margin: 8px 12px; font-size: 11pt; line-height: 1.5; }
    li { margin: 4px 12px 4px 28px; font-size: 11pt; line-height: 1.5; }
    ol, ul { margin: 8px 0; padding: 0; }
    @page { margin: 1.5cm; }
    @media print { body { max-width: 100%; padding: 0; } }
  </style>
</head>
<body>
  <h1>${title}</h1>
  <div class="subtitle">Patient: ${patientName} &nbsp;|&nbsp; Date: ${new Date().toLocaleDateString()}</div>
  ${sectionsHtml}
</body>
</html>`;

    const printWindow = window.open("", "_blank", "width=800,height=900");
    if (!printWindow) {
      toast.error("Print blocked", "Allow pop-ups for this page to enable printing.");
      return;
    }
    printWindow.document.write(html);
    printWindow.document.close();
    printWindow.focus();
    setTimeout(() => {
      printWindow.print();
    }, 500);
  }, [isAmharic, amharicContent, amharicTitle, editableSections, material, patientName]);

  const handleEmail = useCallback(() => {
    const content = isAmharic && amharicContent
      ? amharicContent
      : sectionsToContent(editableSections);
    const subject = encodeURIComponent(`Health Information — ${patientName}`);
    const body = encodeURIComponent(`Dear ${patientName},\n\nHere is your health information:\n\n${content}`);
    window.open(`mailto:?subject=${subject}&body=${body}`);
  }, [isAmharic, amharicContent, editableSections, patientName]);

  const updateSection = (index: number, newBody: string) => {
    setEditableSections((prev) => prev.map((s, i) => i === index ? { ...s, body: newBody } : s));
  };

  const displayContent = isAmharic && amharicContent
    ? parseSections(amharicContent)
    : editableSections;
  const displayTitle = isAmharic && amharicTitle ? amharicTitle : material?.title;
  const displayDate = new Date().toLocaleDateString(isAmharic ? "am-ET" : "en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });

  return (
    <Workspace
        open={open}
        onClose={onClose}
        title="Create Care Guide with AI"
        subtitle={`Health information for ${patientName}`}
        wide
        headerAction={
          material ? (
            isAmharic ? (
              <Button
                type="button"
                className="inline-flex items-center gap-1.5 rounded-xl bg-[hsl(var(--primary))] px-4 py-2 text-sm font-medium text-white hover:opacity-90"
                onClick={() => setIsAmharic(false)}
              >
                <ArrowLeft className="size-4" />
                Back to English
              </Button>
            ) : (
              <Button
                type="button"
                className="inline-flex items-center gap-1.5 rounded-xl bg-[hsl(var(--primary))] px-4 py-2 text-sm font-medium text-white hover:opacity-90"
                disabled={isTranslating}
                onClick={handleTranslate}
              >
                {isTranslating ? <Loader2 className="size-4 animate-spin" /> : <Globe className="size-4" />}
                {isTranslating ? "Translating..." : "Translate to Amharic"}
              </Button>
            )
          ) : null
        }
        footer={
          <div className="flex w-full items-center justify-between gap-2">
            <div className="flex items-center gap-2 no-print">
              {material && (
                <>
                  <Button variant="outline" size="sm" className="gap-1.5" onClick={handlePrint}>
                    <Printer className="size-3.5" /> Print
                  </Button>
                  <Button variant="outline" size="sm" className="gap-1.5" onClick={handleEmail}>
                    <Mail className="size-3.5" /> Email
                  </Button>
                </>
              )}
            </div>
            <div className="flex items-center gap-2">
              <Button variant="secondary" onClick={onClose}>Close</Button>
              <Button onClick={onRun} disabled={isLoading}>
                {isLoading ? <Loader2 className="mr-2 size-4 animate-spin" /> : <RefreshCw className="mr-2 size-4" />}
                {trace ? "Regenerate" : "Create Material"}
              </Button>
            </div>
          </div>
        }
      >
        <div className="space-y-3" id="print-material-root">
          {/* Info banner */}
          <div className="no-print flex items-center gap-2.5 rounded-xl border border-teal-100 bg-teal-50 px-4 py-2.5">
            <BookOpen className="size-4 shrink-0 text-teal-500" />
            <p className="text-xs text-teal-800">
              Patient education material grounded in WHO/MSF guidelines. Review and edit before printing or sending.
            </p>
          </div>

          {/* Loading */}
          {(isLoading || trace?.status === "running") && (
            <div className="no-print flex items-center gap-3 rounded-xl border border-teal-100 bg-teal-50/60 px-4 py-3 text-sm text-teal-700">
              <Loader2 className="size-4 animate-spin text-teal-500" />
              <span>Gemma 4 E4B is reasoning and searching WHO/MSF guidelines…</span>
            </div>
          )}

          {/* Trace events (collapsed by default when material exists) */}
          <details open={!material} className="no-print group rounded-xl border border-[var(--clinic-border)] bg-white">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3">
              <span className="flex items-center gap-2 text-sm font-semibold text-[var(--clinic-ink)]">
                <ChevronDown className="size-4 shrink-0 transition-transform group-open:rotate-180 text-[hsl(var(--muted-foreground))]" />
                <Wrench className="size-4 shrink-0 text-[hsl(var(--muted-foreground))]" />
                How this was generated
              </span>
              <Badge variant={trace?.status === "completed" ? "success" : "info"} className="shrink-0 text-[10px] uppercase">
                {groupTraceEvents(trace?.events ?? []).length} steps
              </Badge>
            </summary>
            <div className="border-t border-[var(--clinic-border)] px-4 py-3 space-y-2">
              {trace?.events.length ? (
                groupTraceEvents(trace.events).map((group, i) => (
                  <TraceGroupRow key={i} group={group} />
                ))
              ) : (
                <p className="text-sm text-[hsl(var(--muted-foreground))]">
                  Click <strong>Create Material</strong> to generate patient education content.
                </p>
              )}
            </div>
          </details>

          {/* Material */}
          {material && (
            <div className="print-material space-y-3">

              {/* Title header card */}
              <div className="rounded-2xl bg-gradient-to-br from-teal-600 to-teal-700 px-6 py-5 text-white shadow-md">
                {/* Top row: label left */}
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-2 text-teal-200 text-[11px] font-semibold uppercase tracking-widest">
                    <BookOpen className="size-3.5 shrink-0" />
                    {isAmharic ? "የታካሚ የጤና መረጃ" : "Patient Health Information"}
                    {isAmharic && (
                      <Badge variant="outline" className="ml-1 text-[9px] uppercase border-teal-400/60 text-teal-100">
                        አማርኛ
                      </Badge>
                    )}
                  </div>
                </div>
                <h2 className="mt-2 text-xl font-bold leading-snug tracking-tight">{displayTitle}</h2>
                <div className="mt-1.5 flex items-center gap-3 text-sm text-teal-100">
                  <span>{patientName}</span>
                  <span className="opacity-40">·</span>
                  <span>{displayDate}</span>
                </div>
              </div>

              {/* Section cards
                  Config is looked up by the ORIGINAL English section order (index into editableSections)
                  so translated titles still receive the correct colour scheme. */}
              {displayContent.map(({ title, body }, index) => {
                const englishKey = editableSections[index]?.title ?? title;
                const cfg = SECTION_CONFIG[englishKey] ?? {
                  icon: null,
                  borderClass: "border-[var(--clinic-border)]",
                  bgClass: "bg-[var(--clinic-surface)]",
                  labelClass: "text-[var(--clinic-slate)]",
                  textClass: "text-[var(--clinic-ink)]",
                };
                const isEditing = editingIndex === index && !isAmharic;

                return (
                  <div
                    key={title}
                    className={`overflow-hidden rounded-2xl border-2 ${cfg.borderClass} bg-white shadow-sm`}
                  >
                    {/* Section header */}
                    <div className={`flex items-center justify-between px-5 py-3 ${cfg.bgClass} border-b-2 ${cfg.borderClass}`}>
                      <div className={`flex items-center gap-2.5 text-[11px] font-bold uppercase tracking-widest ${cfg.labelClass}`}>
                        {cfg.icon}
                        {title}
                      </div>
                      {!isAmharic && (
                        <button
                          type="button"
                          onClick={() => setEditingIndex(isEditing ? null : index)}
                          className={`no-print flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[11px] font-medium transition-colors ${
                            isEditing
                              ? "bg-[var(--clinic-blue)] text-white"
                              : "text-[hsl(var(--muted-foreground))] hover:bg-[hsl(var(--muted))]"
                          }`}
                        >
                          <Edit3 className="size-3" />
                          {isEditing ? "Done" : "Edit"}
                        </button>
                      )}
                    </div>

                    {/* Section body */}
                    <div className="px-5 py-4">
                      {isEditing ? (
                        <Textarea
                          value={body}
                          onChange={(e) => updateSection(index, e.target.value)}
                          className={`min-h-[140px] text-sm leading-relaxed ${cfg.textClass} bg-white/80`}
                          autoFocus
                        />
                      ) : (
                        <MaterialBodyRenderer body={body} textClass={cfg.textClass} />
                      )}
                    </div>
                  </div>
                );
              })}

              {/* Edit hint */}
              {!isAmharic && (
                <p className="no-print pb-1 text-center text-[10px] text-[hsl(var(--muted-foreground))]">
                  Click <Edit3 className="inline size-3" /> on any section to edit before printing
                </p>
              )}
            </div>
          )}
        </div>
      </Workspace>
  );
}

// ── Section body renderer ────────────────────────────────────────────────

function MaterialBodyRenderer({ body, textClass }: { body: string; textClass: string }) {
  const lines = body.split("\n");
  const nodes: React.ReactNode[] = [];
  let listItems: { ordered: boolean; text: string; num: number }[] = [];
  let key = 0;

  const flushList = () => {
    if (!listItems.length) return;
    if (listItems[0].ordered) {
      nodes.push(
        <ol key={key++} className="mt-2 space-y-2 pl-1">
          {listItems.map((item, i) => (
            <li key={i} className={`flex gap-3 text-sm leading-relaxed ${textClass}`}>
              <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-current/10 text-[10px] font-bold">
                {item.num + i === item.num ? item.num : item.num + i}
              </span>
              <span dangerouslySetInnerHTML={{ __html: renderInline(item.text) }} />
            </li>
          ))}
        </ol>,
      );
    } else {
      nodes.push(
        <ul key={key++} className="mt-2 space-y-2">
          {listItems.map((item, i) => (
            <li key={i} className={`flex gap-3 text-sm leading-relaxed ${textClass}`}>
              <span className="mt-[7px] size-2 shrink-0 rounded-full bg-current opacity-40" />
              <span dangerouslySetInnerHTML={{ __html: renderInline(item.text) }} />
            </li>
          ))}
        </ul>,
      );
    }
    listItems = [];
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) { flushList(); continue; }
    const numMatch = trimmed.match(/^(\d+)\.\s+(.*)/);
    const bulletMatch = trimmed.match(/^[-*•]\s+(.*)/);
    if (numMatch) {
      const startNum = parseInt(numMatch[1]);
      listItems.push({ ordered: true, text: numMatch[2], num: listItems.length === 0 ? startNum : listItems[0].num + listItems.length });
    } else if (bulletMatch) {
      listItems.push({ ordered: false, text: bulletMatch[1], num: 0 });
    } else {
      flushList();
      nodes.push(
        <p
          key={key++}
          className={`mt-1.5 text-sm leading-relaxed ${textClass}`}
          dangerouslySetInnerHTML={{ __html: renderInline(trimmed) }}
        />,
      );
    }
  }
  flushList();
  return <div className="space-y-0.5">{nodes}</div>;
}

function renderInline(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+?)\*/g, "<em>$1</em>");
}

// ── Trace grouping ────────────────────────────────────────────────────────

type TraceEvent = { type: string; title: string; detail: string; timestamp: string; payload?: Record<string, unknown> };

type TraceGroup =
  | { kind: "tool"; call: TraceEvent; result: TraceEvent | null }
  | { kind: "reasoning"; event: TraceEvent }
  | { kind: "summary"; event: TraceEvent };

function groupTraceEvents(events: TraceEvent[]): TraceGroup[] {
  const groups: TraceGroup[] = [];
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
      // standalone middleware_result with no prior tool call
      groups.push({ kind: "tool", call: ev, result: null });
      i++;
    }
  }
  return groups;
}

// ── Trace group row ───────────────────────────────────────────────────────

function TraceGroupRow({ group }: { group: TraceGroup }) {
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

  // tool group
  const { call, result } = group;
  const funcName = call.title;
  const args = call.payload?.arguments as Record<string, unknown> | undefined;
  const query = (args?.query as string | undefined) ?? (call.payload?.query as string | undefined);
  const headerLabel = query ? `${funcName}: "${query.slice(0, 60)}${query.length > 60 ? "…" : ""}"` : funcName;

  const hitsReturned = result?.payload?.hits_returned as number | undefined;
  const topHit = result?.payload?.top_hit as string | undefined;
  const resultDetail = result?.detail ?? "";

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
        {/* Input */}
        {args && Object.keys(args).length > 0 && (
          <div className="px-3 py-2.5 space-y-1">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">Input</p>
            <pre className="text-[11px] leading-relaxed text-[var(--clinic-ink)] whitespace-pre-wrap break-all font-mono bg-[hsl(var(--muted))] rounded px-2 py-1.5 max-h-32 overflow-y-auto">
              {JSON.stringify(args, null, 2)}
            </pre>
          </div>
        )}
        {/* Output */}
        {result && (
          <div className="px-3 py-2.5 space-y-1">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">Result</p>
            <p className="text-xs leading-relaxed text-[var(--clinic-ink)]">{resultDetail}</p>
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
