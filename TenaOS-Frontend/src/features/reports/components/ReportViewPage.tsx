import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Calendar, Filter, Play, SlidersHorizontal } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ErrorState } from "@/components/common/ErrorState";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useApplyReportOperations, useReportDraft, useReportResult, useRunReport } from "../hooks/useReportBuilder";
import { ReportResultPanel } from "./ReportResultPanel";

const DATE_RANGE_OPTIONS = [
  { value: "last 30 days", label: "Last 30 days" },
  { value: "last 3 months", label: "Last 3 months" },
  { value: "last 6 months", label: "Last 6 months" },
  { value: "last 12 months", label: "Last 12 months" },
  { value: "this month", label: "This month" },
  { value: "last month", label: "Last month" },
  { value: "this quarter", label: "This quarter" },
  { value: "last quarter", label: "Last quarter" },
  { value: "this year", label: "This year" },
  { value: "last year", label: "Last year" },
] as const;

export function ReportViewPage() {
  const { draftId } = useParams<{ draftId: string }>();
  const navigate = useNavigate();
  const draft = useReportDraft(draftId);
  const result = useReportResult(draftId);
  const applyOperations = useApplyReportOperations(draftId);
  const runReport = useRunReport(draftId);
  const [dateRange, setDateRange] = useState("");

  useEffect(() => {
    if (draft.data?.spec.dateRangeLabel) setDateRange(draft.data.spec.dateRangeLabel);
  }, [draft.data?.spec.dateRangeLabel]);

  if (draft.isError) {
    return <ErrorState title="Could not load report" onRetry={() => draft.refetch()} />;
  }

  if (draft.isLoading || !draft.data) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-16 rounded-2xl" />
        <Skeleton className="h-28 rounded-2xl" />
        <Skeleton className="h-80 rounded-2xl" />
      </div>
    );
  }

  const spec = draft.data.spec;
  const resolvedResult = result.data?.result ?? draft.data.lastResult ?? null;
  const isUpdating = applyOperations.isPending || runReport.isPending;
  const applyDateAndRun = async () => {
    const text = dateRange.trim();
    if (!text) return;
    await applyOperations.mutateAsync([{ op: "set_date_range", text }]);
    await runReport.mutateAsync();
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Button variant="ghost" className="mb-2 px-0" onClick={() => navigate("/reports")}>
            <ArrowLeft size={14} className="mr-1.5" /> Reports
          </Button>
          <h1 className="text-xl font-semibold text-[var(--clinic-ink)]">{draft.data.name}</h1>
          <p className="text-sm text-[hsl(var(--muted-foreground))]">
            {draft.data.description || "Published report"}
          </p>
        </div>
        <Button className="bg-[hsl(var(--primary))] text-white hover:opacity-90" onClick={() => navigate(`/reports/${draft.data.draftId}`)}>
          Edit report
        </Button>
      </div>

      <Card>
        <CardContent className="p-4 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="flex items-center gap-2 text-sm font-semibold text-[var(--clinic-ink)]">
                <Filter size={15} /> Report filters
              </div>
              <p className="text-xs text-[hsl(var(--muted-foreground))]">
                Adjust the published report timeframe, then rerun with the same CIEL-backed definition.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Select value={dateRange} onValueChange={setDateRange} disabled={isUpdating}>
                <SelectTrigger className="w-56 border-[#14b8a6] bg-[#dff6f3] text-[var(--clinic-ink)]">
                  <span className="flex min-w-0 items-center gap-2">
                    <Calendar size={14} className="shrink-0 text-[var(--clinic-blue)]" />
                    <SelectValue placeholder="Select timeframe" />
                  </span>
                </SelectTrigger>
                <SelectContent>
                  {DATE_RANGE_OPTIONS.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button
                type="button"
                className="bg-[hsl(var(--primary))] text-white hover:opacity-90"
                disabled={isUpdating || !dateRange}
                onClick={applyDateAndRun}
              >
                Apply & rerun
              </Button>
              <Button type="button" variant="secondary" disabled={isUpdating} onClick={() => runReport.mutate()}>
                <Play size={14} className="mr-1.5" />
                Rerun
              </Button>
            </div>
          </div>
          <div className="flex flex-wrap gap-2 rounded-xl border border-[#b2e8e2] bg-[#dff6f3] p-3">
            <Badge variant="secondary">{draft.data.reportType}</Badge>
            <Badge variant="outline">{spec.dateRangeLabel ?? formatRange(spec)}</Badge>
            <Badge variant="outline">Join: {spec.joinMode.toUpperCase()}</Badge>
            {spec.filters.map((filter) => (
              <Badge key={filter.filterId} variant="outline">
                {filter.label}{filter.valueBool === true ? "=Yes" : filter.valueBool === false ? "=No" : ""}
                {filter.conceptIds && filter.conceptIds.length > 1 ? ` · ${filter.conceptIds.length} CIEL concepts` : ""}
              </Badge>
            ))}
          </div>
          {spec.groupBy.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 text-xs text-[hsl(var(--muted-foreground))]">
              <SlidersHorizontal size={13} />
              Group by:
              {spec.groupBy.map((group) => (
                <Badge key={`${group.dimension}-${group.conceptId ?? ""}`} variant="secondary" className="text-xs">
                  {group.label || group.dimension}
                </Badge>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <ReportResultPanel
        result={resolvedResult}
        spec={spec}
        status={result.data?.status ?? draft.data.status}
        lastRunAt={result.data?.lastRunAt ?? draft.data.lastRunAt}
      />
    </div>
  );
}

function formatRange(spec: { dateFrom: string | null; dateTo: string | null }) {
  if (!spec.dateFrom && !spec.dateTo) return "No date range";
  return `${spec.dateFrom ?? "?"} to ${spec.dateTo ?? "?"}`;
}
