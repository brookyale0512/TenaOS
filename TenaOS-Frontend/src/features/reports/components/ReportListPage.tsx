import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { BarChart3, Plus, ChartPie, Users, Activity, Pencil, Trash2 } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/common/ErrorState";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { useDeleteReportDraft, useReportDraftList } from "../hooks/useReportBuilder";
import type { ReportDraft, ReportType } from "../types/reportBuilder";

const TYPE_ICONS: Record<ReportType, typeof BarChart3> = {
  count: BarChart3,
  cohort: Users,
  indicator: Activity,
  pivot: ChartPie,
};

const TYPE_LABELS: Record<ReportType, string> = {
  count: "Count",
  cohort: "Cohort",
  indicator: "Indicator",
  pivot: "Pivot",
};

export function ReportListPage() {
  const navigate = useNavigate();
  const { data: drafts, isLoading, isError, refetch } = useReportDraftList();
  const deleteReport = useDeleteReportDraft();
  const [pendingDelete, setPendingDelete] = useState<ReportDraft | null>(null);

  const confirmDelete = () => {
    if (!pendingDelete) return;
    deleteReport.mutate(pendingDelete.draftId, {
      onSettled: () => setPendingDelete(null),
    });
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold text-[var(--clinic-ink)]">Manage Reports</h1>
          <p className="text-sm text-[hsl(var(--muted-foreground))]">
            Build, publish, edit, and archive CIEL-backed reports from captured form observations.
          </p>
        </div>
        <Button onClick={() => navigate("/reports/new")}>
          <Plus size={14} className="mr-1.5" /> Create with assistant
        </Button>
      </div>

      {isError ? (
        <ErrorState
          title="Could not load report drafts"
          description="The TenaAgent service didn't return any drafts. Make sure /agent-api is reachable."
          onRetry={() => refetch()}
        />
      ) : isLoading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array(6).fill(0).map((_, i) => <Skeleton key={i} className="h-32 rounded-3xl" />)}
        </div>
      ) : drafts && drafts.length > 0 ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {drafts.map((draft) => {
            const Icon = TYPE_ICONS[draft.reportType] ?? BarChart3;
            return (
              <ReportManageCard
                key={draft.draftId}
                draft={draft}
                Icon={Icon}
                onOpen={() => navigate(`/reports/${draft.draftId}`)}
                onDelete={() => setPendingDelete(draft)}
              />
            );
          })}
        </div>
      ) : (
        <Card>
          <CardContent className="p-10 text-center text-[hsl(var(--muted-foreground))]">
            <BarChart3 size={28} className="mx-auto mb-3 text-[var(--clinic-slate)]" />
            <div className="text-sm font-semibold text-[var(--clinic-ink)]">No reports yet</div>
            <p className="text-xs mt-1 mb-4">
              Ask the assistant a question like "how many patients had cough last quarter" or
              "TB cases by sex and age group" to build your first report.
            </p>
            <Button onClick={() => navigate("/reports/new")}>
              <Plus size={14} className="mr-1.5" /> Create with assistant
            </Button>
          </CardContent>
        </Card>
      )}

      <AlertDialog open={Boolean(pendingDelete)} onOpenChange={(open) => !open && setPendingDelete(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete "{pendingDelete?.name}"?</AlertDialogTitle>
            <AlertDialogDescription>
              This archives the report. Published users will no longer see it in the Reports menu.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => setPendingDelete(null)}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-red-500 text-white hover:bg-red-600"
              onClick={confirmDelete}
              disabled={deleteReport.isPending}
            >
              {deleteReport.isPending ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function ReportManageCard({
  draft,
  Icon,
  onOpen,
  onDelete,
}: {
  draft: ReportDraft;
  Icon: typeof BarChart3;
  onOpen: () => void;
  onDelete: () => void;
}) {
  return (
    <Card
      className="h-full cursor-pointer overflow-hidden border-[var(--clinic-teal)] bg-[var(--clinic-mint)] transition-all hover:shadow-md"
      onClick={onOpen}
    >
      <CardContent className="flex h-full flex-col p-4">
        <div className="flex items-start justify-between gap-2">
          <div className="h-9 w-9 rounded-xl bg-white text-[var(--clinic-blue)] flex items-center justify-center shrink-0 ring-2 ring-[var(--clinic-teal)]/30">
            <Icon size={16} />
          </div>
          <div className="flex items-center gap-1">
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 text-[var(--clinic-slate)] hover:bg-white hover:text-[var(--clinic-blue)]"
                title="Edit report"
                onClick={(event) => {
                  event.stopPropagation();
                  onOpen();
                }}
              >
                <Pencil size={13} />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 text-[var(--clinic-slate)] hover:bg-red-50 hover:text-red-500"
                title="Delete report"
                onClick={(event) => {
                  event.stopPropagation();
                  onDelete();
                }}
              >
                <Trash2 size={13} />
              </Button>
          </div>
        </div>
        <h3 className="font-semibold text-[var(--clinic-ink)] mt-3 text-sm line-clamp-2">{draft.name}</h3>
        {draft.description && (
          <p className="mt-1 line-clamp-2 text-xs text-[var(--clinic-slate)]">{draft.description}</p>
        )}
        <div className="flex-1" />
        <div className="mt-3 flex items-center justify-between gap-3">
          <Badge
            variant={draft.published ? "success" : "secondary"}
            className="shrink-0 border-[var(--clinic-teal)]/30 bg-white text-xs text-[var(--clinic-blue)]"
          >
            {draft.published ? "Published" : "Draft"}
          </Badge>
          <span className="truncate text-right text-xs text-[var(--clinic-slate)]">
            {TYPE_LABELS[draft.reportType]}
          </span>
        </div>
      </CardContent>
    </Card>
  );
}
