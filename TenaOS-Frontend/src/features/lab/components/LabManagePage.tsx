import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  FlaskConical,
  Loader2,
  Send,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { InlineMarkup } from "@/lib/render/InlineMarkup";
import {
  useLabCatalog,
  useAddLabTest,
  useConfirmLabTest,
  useRemoveLabTest,
  type LabCatalogCandidate,
} from "../hooks/useLabCatalog";

// ── Category colors ───────────────────────────────────────────────────────

const CATEGORY_COLORS: Record<string, string> = {
  Hematology:   "border-red-200 bg-red-50 text-red-700",
  Chemistry:    "border-blue-200 bg-blue-50 text-blue-700",
  "HIV/TB":     "border-purple-200 bg-purple-50 text-purple-700",
  Microbiology: "border-amber-200 bg-amber-50 text-amber-700",
  Urinalysis:   "border-cyan-200 bg-cyan-50 text-cyan-700",
  Hormones:     "border-pink-200 bg-pink-50 text-pink-700",
  Serology:     "border-indigo-200 bg-indigo-50 text-indigo-700",
  Imaging:      "border-slate-200 bg-slate-50 text-slate-700",
  Other:        "border-[var(--clinic-border)] bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))]",
};

// ── Quick-add suggestions ─────────────────────────────────────────────────

const QUICK_ADDS = [
  "Complete blood count",
  "Serum glucose",
  "Serum creatinine",
  "CD4 count",
  "HIV viral load",
  "Liver function tests",
  "Serum electrolytes",
  "Urinalysis",
  "Haemoglobin",
  "Malaria antigen",
];

// ── Chat message types ────────────────────────────────────────────────────

type ChatMsg =
  | { role: "user"; text: string }
  | { role: "assistant"; text: string; status: "added" | "already_exists" | "not_found" }
  | { role: "candidates"; description: string; candidates: LabCatalogCandidate[] };

// ── Main page — split-pane, mirrors FormBuilderWorkspace ─────────────────

export function LabManagePage() {
  const navigate = useNavigate();
  const { data: catalog, isLoading } = useLabCatalog();
  const addTest = useAddLabTest();
  const confirmTest = useConfirmLabTest();
  const removeTest = useRemoveLabTest();

  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const chatEndRef = useRef<HTMLDivElement>(null);

  // Resizable chat panel — same as FormBuilderWorkspace
  const [chatWidth, setChatWidth] = useState(380);
  const dragRef = useRef<{ startX: number; startW: number } | null>(null);
  const onDragStart = (e: React.PointerEvent) => {
    dragRef.current = { startX: e.clientX, startW: chatWidth };
    (e.target as Element).setPointerCapture(e.pointerId);
  };
  const onDragMove = (e: React.PointerEvent) => {
    if (!dragRef.current) return;
    const delta = dragRef.current.startX - e.clientX;
    setChatWidth(Math.max(280, Math.min(600, dragRef.current.startW + delta)));
  };
  const onDragEnd = () => { dragRef.current = null; };

  const catalogEntries = catalog ? Object.entries(catalog) : [];
  const totalTests = catalogEntries.reduce((sum, [, tests]) => sum + tests.length, 0);

  const handleSend = async (text: string) => {
    if (!text.trim()) return;
    const userMsg = text.trim();
    setInput("");
    setMessages((prev) => [...prev, { role: "user", text: userMsg }]);
    setTimeout(() => chatEndRef.current?.scrollIntoView({ behavior: "smooth" }), 50);

    const result = await addTest.mutateAsync(userMsg);
    const interpreted = result.interpreted;  // Gemma 4's canonical extraction
    const prefix = interpreted ? `*(Gemma 4 interpreted: "${interpreted}")* ` : "";

    if (result.status === "added") {
      setMessages((prev) => [...prev, {
        role: "assistant", status: "added",
        text: `${prefix}Added **${result.entry?.displayName}** to ${result.entry?.category}.${
          result.entry?.hiNormal != null
            ? ` Reference range: ${result.entry.lowNormal ?? "—"}–${result.entry.hiNormal} ${result.entry.units ?? ""}`
            : " No reference range in CIEL — you can set it manually if needed."
        }`,
      }]);
    } else if (result.status === "already_exists") {
      setMessages((prev) => [...prev, {
        role: "assistant", status: "already_exists",
        text: `${prefix}**${result.entry?.displayName}** is already in the catalog.`,
      }]);
    } else if (result.status === "not_found") {
      setMessages((prev) => [...prev, {
        role: "assistant", status: "not_found",
        text: `${prefix}No CIEL concept found. Try a more specific clinical name (e.g. "haemoglobin", "serum creatinine").`,
      }]);
    } else if (result.status === "candidates") {
      setMessages((prev) => [...prev, {
        role: "candidates",
        description: interpreted ?? userMsg,
        candidates: result.candidates ?? [],
      }]);
    }
    setTimeout(() => chatEndRef.current?.scrollIntoView({ behavior: "smooth" }), 100);
  };

  const handleConfirm = async (candidate: LabCatalogCandidate) => {
    // Replace the candidates message with a user confirmation + result
    setMessages((prev) => [
      ...prev.filter((m) => m.role !== "candidates"),
      { role: "user", text: `Add ${candidate.displayName}` },
    ]);
    const result = await confirmTest.mutateAsync(candidate);
    if (result.status === "added") {
      setMessages((prev) => [...prev, {
        role: "assistant", status: "added",
        text: `Added **${result.entry?.displayName}** to ${result.entry?.category}.`,
      }]);
    }
    setTimeout(() => chatEndRef.current?.scrollIntoView({ behavior: "smooth" }), 100);
  };

  const handleRemove = async (uuid: string) => {
    await removeTest.mutateAsync(uuid);
  };

  return (
    <div
      className="-mx-4 -my-4 md:-mx-6 md:-my-6 flex flex-col lg:flex-row overflow-hidden lg:h-[calc(100svh-4rem)]"
      onPointerMove={onDragMove}
      onPointerUp={onDragEnd}
    >
      {/* ── Left: catalog view ─────────────────────────────────────────── */}
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
        {/* Top bar */}
        <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 border-b shrink-0">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <FlaskConical size={18} className="shrink-0 text-[var(--clinic-blue)]" />
              <h1 className="text-xl font-semibold text-[var(--clinic-ink)]">Manage Lab Tests</h1>
              {totalTests > 0 && (
                <Badge variant="secondary" className="text-xs">{totalTests} configured</Badge>
              )}
            </div>
            <p className="text-sm text-[hsl(var(--muted-foreground))]">
              Use the chat to add tests. Gemma 4 + CIEL resolves concepts and reference ranges automatically.
            </p>
          </div>
          <Button variant="secondary" onClick={() => navigate("/labs")}>
            <ArrowLeft size={14} className="mr-1.5 shrink-0" /> Back
          </Button>
        </div>

        {/* Catalog content */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {isLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-28 w-full rounded-2xl" />)}
            </div>
          ) : totalTests === 0 ? (
            <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed py-20 text-center">
              <div className="mb-3 rounded-full bg-[var(--clinic-ice)] p-5">
                <FlaskConical size={28} className="text-[var(--clinic-slate)]" />
              </div>
              <p className="text-sm font-medium text-[var(--clinic-ink)]">No lab tests configured yet</p>
              <p className="mt-1 text-xs text-[hsl(var(--muted-foreground))] max-w-xs">
                Type a test name in the chat on the right, e.g. "complete blood count" or "serum creatinine".
              </p>
            </div>
          ) : (
            catalogEntries.map(([category, tests]) => {
              if (tests.length === 0) return null;
              const catColor = CATEGORY_COLORS[category] ?? CATEGORY_COLORS["Other"];
              return (
                <div key={category} className="rounded-2xl border overflow-hidden">
                  <div className={`flex items-center gap-2 px-4 py-2.5 border-b ${catColor}`}>
                    <FlaskConical size={13} />
                    <span className="text-xs font-bold uppercase tracking-wide">{category}</span>
                    <Badge variant="outline" className="ml-auto text-[9px]">{tests.length}</Badge>
                  </div>
                  <div className="divide-y bg-white">
                    {tests.map((test) => (
                      <div key={test.uuid} className="flex items-center justify-between px-4 py-3">
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-sm font-medium text-[var(--clinic-ink)]">{test.displayName}</span>
                            {(test.lowNormal != null || test.hiNormal != null) && (
                              <span className="flex items-center gap-1 rounded-full bg-emerald-50 border border-emerald-200 px-2 py-0.5 text-[9px] text-emerald-700">
                                <CheckCircle2 size={9} />
                                {test.lowNormal ?? "—"}–{test.hiNormal ?? "—"}{test.units ? ` ${test.units}` : ""}
                              </span>
                            )}
                          </div>
                          <p className="mt-0.5 text-[10px] text-[hsl(var(--muted-foreground))]">
                            CIEL {test.conceptId}{test.units && !test.lowNormal ? ` · ${test.units}` : ""}
                          </p>
                        </div>
                        <button type="button" onClick={() => handleRemove(test.uuid)}
                          disabled={removeTest.isPending}
                          className="ml-4 shrink-0 rounded-lg p-1.5 text-[hsl(var(--muted-foreground))] transition-colors hover:bg-red-50 hover:text-red-500 disabled:opacity-50">
                          <Trash2 size={14} />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* ── Drag handle ──────────────────────────────────────────────────── */}
      <div onPointerDown={onDragStart}
        className="hidden lg:flex w-1.5 shrink-0 cursor-col-resize items-center justify-center group"
        title="Drag to resize">
        <div className="w-px h-full bg-[var(--clinic-border)] group-hover:bg-[var(--clinic-slate)]/50 transition-colors" />
      </div>

      {/* ── Right: chat panel ─────────────────────────────────────────────── */}
      <div className="shrink-0 border-t lg:border-t-0 flex flex-col min-h-[28rem] lg:min-h-0 border-l"
        style={{ width: `${chatWidth}px` }}>

        {/* Chat header */}
        <div className="flex items-center gap-2 border-b px-4 py-3 shrink-0">
          <FlaskConical size={15} className="text-[var(--clinic-blue)]" />
          <span className="text-sm font-semibold text-[var(--clinic-ink)]">Add Lab Tests</span>
          <span className="ml-auto text-[10px] text-[hsl(var(--muted-foreground))]">
            Powered by Gemma 4 + CIEL
          </span>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {messages.length === 0 && (
            <div className="space-y-3">
              <p className="text-xs text-[hsl(var(--muted-foreground))]">
                Type any lab test name and I'll find the CIEL concept, assign the category,
                and pull reference ranges automatically.
              </p>
              <div>
                <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
                  Quick add
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {QUICK_ADDS.map((term) => (
                    <button key={term} type="button"
                      onClick={() => handleSend(term)}
                      disabled={addTest.isPending}
                      className="rounded-full border border-[var(--clinic-border)] bg-[hsl(var(--muted)/0.4)] px-2.5 py-1 text-[11px] font-medium text-[hsl(var(--muted-foreground))] transition-colors hover:border-[var(--clinic-blue)] hover:bg-blue-50 hover:text-[var(--clinic-blue)] disabled:opacity-50">
                      {term}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          {messages.map((msg, i) => {
            if (msg.role === "user") {
              return (
                <div key={i} className="flex justify-end">
                  <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-[var(--clinic-blue)] px-3 py-2 text-sm text-white">
                    {msg.text}
                  </div>
                </div>
              );
            }
            if (msg.role === "assistant") {
              const isAdded = msg.status === "added";
              const isError = msg.status === "not_found";
              return (
                <div key={i} className="flex justify-start">
                  <div className={`max-w-[90%] rounded-2xl rounded-bl-sm px-3 py-2 text-sm ${
                    isAdded ? "border border-emerald-200 bg-emerald-50 text-emerald-900"
                    : isError ? "border border-slate-200 bg-slate-50 text-slate-700"
                    : "border border-[var(--clinic-border)] bg-white text-[var(--clinic-ink)]"
                  }`}>
                    <InlineMarkup text={msg.text} labNotes />
                  </div>
                </div>
              );
            }
            if (msg.role === "candidates") {
              return (
                <div key={i} className="space-y-2">
                  <div className="flex justify-start">
                    <div className="max-w-[90%] rounded-2xl rounded-bl-sm border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                      <div className="flex items-center gap-1.5 mb-2 font-medium">
                        <AlertCircle size={13} />
                        Multiple matches — choose one:
                      </div>
                      <div className="space-y-1.5">
                        {msg.candidates.map((c) => (
                          <button key={c.conceptId} type="button"
                            onClick={() => handleConfirm(c)}
                            disabled={confirmTest.isPending}
                            className="flex w-full items-center justify-between rounded-xl border border-amber-200 bg-white px-3 py-2 text-left text-xs transition-colors hover:border-[var(--clinic-blue)] hover:bg-blue-50">
                            <div>
                              <p className="font-medium text-[var(--clinic-ink)]">{c.displayName}</p>
                              <p className="text-[10px] text-[hsl(var(--muted-foreground))]">
                                CIEL {c.conceptId} · {c.category}
                              </p>
                            </div>
                            <span className="text-[var(--clinic-blue)] font-bold text-base ml-2">+</span>
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              );
            }
            return null;
          })}

          {addTest.isPending && (
            <div className="flex justify-start">
              <div className="rounded-2xl rounded-bl-sm border border-[var(--clinic-border)] bg-white px-4 py-2.5">
                <Loader2 size={14} className="animate-spin text-[var(--clinic-blue)]" />
              </div>
            </div>
          )}
          <div ref={chatEndRef} />
        </div>

        {/* Input */}
        <div className="border-t p-3 shrink-0">
          <div className="flex gap-2">
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSend(input);
                }
              }}
              placeholder='Type a lab test name, e.g. "haemoglobin"'
              className="min-h-[2.5rem] max-h-[6rem] resize-none text-sm"
              disabled={addTest.isPending}
              rows={1}
            />
            <Button size="icon" className="shrink-0 h-10 w-10"
              onClick={() => handleSend(input)}
              disabled={!input.trim() || addTest.isPending}>
              {addTest.isPending ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
            </Button>
          </div>
          <p className="mt-1.5 text-[10px] text-[hsl(var(--muted-foreground))]">
            Press Enter to send · Shift+Enter for new line
          </p>
        </div>
      </div>
    </div>
  );
}
