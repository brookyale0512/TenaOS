import { useNavigate } from "react-router-dom";
import { Activity, BarChart3, ChartPie, FileText, Plus, Users } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ErrorState } from "@/components/common/ErrorState";
import { Skeleton } from "@/components/ui/skeleton";
import { usePublishedReportList } from "../hooks/useReportBuilder";
import type { ReportDraft, ReportType } from "../types/reportBuilder";

const TYPE_ICONS: Record<ReportType, typeof BarChart3> = {
  count: BarChart3,
  cohort: Users,
  indicator: Activity,
  pivot: ChartPie,
};

export function PublishedReportListPage() {
  const navigate = useNavigate();
  const { data: reports, isLoading, isError, refetch } = usePublishedReportList();

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold text-[var(--clinic-ink)]">Reports</h1>
          <p className="text-sm text-[hsl(var(--muted-foreground))]">
            Published reports ready for review.
          </p>
        </div>
        <Button onClick={() => navigate("/reports/manage")}>
          Manage reports
        </Button>
      </div>

      {isError ? (
        <ErrorState title="Could not load reports" onRetry={() => refetch()} />
      ) : isLoading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array(6).fill(0).map((_, index) => <Skeleton key={index} className="h-28 rounded-3xl" />)}
        </div>
      ) : reports && reports.length > 0 ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {reports.map((report) => (
            <PublishedReportCard
              key={report.draftId}
              report={report}
              onOpen={() => navigate(`/reports/view/${report.draftId}`)}
            />
          ))}
        </div>
      ) : (
        <Card>
          <CardContent className="p-10 text-center text-[hsl(var(--muted-foreground))]">
            <FileText size={28} className="mx-auto mb-3 text-[var(--clinic-slate)]" />
            <div className="text-sm font-semibold text-[var(--clinic-ink)]">No published reports</div>
            <p className="text-xs mt-1 mb-4">Publish a report from Manage Reports to make it available here.</p>
            <Button onClick={() => navigate("/reports/manage")}>
              <Plus size={14} className="mr-1.5" /> Manage reports
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function PublishedReportCard({ report, onOpen }: { report: ReportDraft; onOpen: () => void }) {
  const Icon = TYPE_ICONS[report.reportType] ?? BarChart3;
  const lastRun = report.lastRunAt ? new Date(report.lastRunAt).toLocaleString() : "never run";
  return (
    <Card className="h-full cursor-pointer overflow-hidden border-[var(--clinic-teal)] bg-[var(--clinic-mint)] transition-all hover:shadow-md" onClick={onOpen}>
      <CardContent className="p-4">
        <div className="flex items-start justify-between gap-2">
          <div className="h-9 w-9 rounded-xl bg-[var(--clinic-mint)] text-[var(--clinic-blue)] flex items-center justify-center shrink-0">
            <Icon size={16} />
          </div>
          <Badge variant="success" className="text-xs">Published</Badge>
        </div>
        <h3 className="font-semibold text-[var(--clinic-ink)] mt-3 text-sm line-clamp-2">{report.name}</h3>
        {report.description && (
          <p className="mt-1 line-clamp-2 text-xs text-[var(--clinic-slate)]">{report.description}</p>
        )}
        <div className="text-xs text-[var(--clinic-slate)] mt-3">Last run: {lastRun}</div>
      </CardContent>
    </Card>
  );
}
