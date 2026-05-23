import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Filter, SlidersHorizontal } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ErrorState } from "@/components/common/ErrorState";
import { Skeleton } from "@/components/ui/skeleton";
import { useReportDraft, useReportResult } from "../hooks/useReportBuilder";
import { ReportResultPanel } from "./ReportResultPanel";

export function ReportViewPage() {
  const { draftId } = useParams<{ draftId: string }>();
  const navigate = useNavigate();
  const draft = useReportDraft(draftId);
  const result = useReportResult(draftId);

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
        <CardContent className="p-4 space-y-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-[var(--clinic-ink)]">
            <Filter size={15} /> Filters
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant="secondary">{draft.data.reportType}</Badge>
            <Badge variant="outline">{spec.dateRangeLabel ?? formatRange(spec)}</Badge>
            <Badge variant="outline">Join: {spec.joinMode.toUpperCase()}</Badge>
            {spec.filters.map((filter) => (
              <Badge key={filter.filterId} variant="outline">
                {filter.label}{filter.valueBool === true ? "=Yes" : filter.valueBool === false ? "=No" : ""}
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
