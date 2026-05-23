import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Users, XCircle, ChevronDown, ChevronUp, History } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
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
import { useActiveVisits, type ActiveVisit } from "../hooks/useVisits";

/** OpenMRS patient.display is "ID - Full Name". Return both parts. */
function parsePatientDisplay(display: string): { name: string; id: string } {
  const idx = display.indexOf(" - ");
  if (idx !== -1) {
    return { id: display.slice(0, idx).trim(), name: display.slice(idx + 3).trim() };
  }
  return { name: display, id: "" };
}
// Canonical single hook — no duplicate
import { useEndVisit } from "@/features/patients/hooks/usePatients";
import { formatDate } from "@/lib/utils";
import { isCurrentActiveVisit, isOpenVisit } from "../utils/visitStatus";

const PREVIEW_LIMIT = 5;

export function ActiveVisitsDashboard() {
  const navigate = useNavigate();
  const { data: visits, isLoading } = useActiveVisits();
  const endVisit = useEndVisit();
  const [pendingEnd, setPendingEnd] = useState<ActiveVisit | null>(null);
  const [showAllActive, setShowAllActive] = useState(false);
  const [showAllPrevious, setShowAllPrevious] = useState(false);

  const currentVisits = visits?.filter((visit) => isCurrentActiveVisit(visit)) ?? [];
  const olderVisits = visits?.filter((visit) => !isCurrentActiveVisit(visit)) ?? [];

  const visibleActive = showAllActive ? currentVisits : currentVisits.slice(0, PREVIEW_LIMIT);
  const visiblePrevious = showAllPrevious ? olderVisits : olderVisits.slice(0, PREVIEW_LIMIT);
  const hiddenActiveCount = currentVisits.length - PREVIEW_LIMIT;
  const hiddenPreviousCount = olderVisits.length - PREVIEW_LIMIT;

  return (
    <div className="space-y-4">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-[var(--clinic-ink)]">Clinical Visits</h1>
          <p className="text-sm text-[hsl(var(--muted-foreground))]">
            Manage active visits and review previous visit records.
          </p>
        </div>
        <Badge variant="info" className="text-sm">
          <Users size={13} className="mr-1" /> {currentVisits.length} active
        </Badge>
      </div>

      {/* ── Active Visits ── */}
      <Card className="border-emerald-200 bg-emerald-50 overflow-hidden">
        <CardHeader className="border-b border-emerald-100 px-4 py-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm font-semibold text-emerald-800 flex items-center gap-2">
              <span className="relative flex size-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex rounded-full size-2 bg-emerald-500" />
              </span>
              Active Visits
            </CardTitle>
            {!isLoading && (
              <span className="text-xs text-emerald-700 font-medium">
                {currentVisits.length} patient{currentVisits.length !== 1 ? "s" : ""} in clinic
              </span>
            )}
          </div>
          <p className="text-xs text-emerald-700/70 mt-0.5">Visits currently in progress — click a row to open the patient chart.</p>
        </CardHeader>

        {isLoading ? (
          <CardContent className="p-4 space-y-2">
            {Array(5).fill(0).map((_, i) => <Skeleton key={i} className="h-12 w-full" />)}
          </CardContent>
        ) : currentVisits.length === 0 ? (
          <CardContent className="py-12 text-center text-sm text-[hsl(var(--muted-foreground))]">
            No active visits at this time.
          </CardContent>
        ) : (
          <>
            <Table>
              <TableHeader>
                <TableRow className="bg-emerald-100/40">
                  <TableHead className="text-xs text-emerald-700">Patient</TableHead>
                  <TableHead className="text-xs text-emerald-700">Visit Type</TableHead>
                  <TableHead className="text-xs text-emerald-700">Location</TableHead>
                  <TableHead className="text-xs text-emerald-700">Started</TableHead>
                  <TableHead className="text-xs text-emerald-700">Encounters</TableHead>
                  <TableHead></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {visibleActive.map((visit) => (
                  <TableRow
                    key={visit.uuid}
                    className="cursor-pointer hover:bg-emerald-100/60"
                    onClick={() => navigate(`/patients/${visit.patient.uuid}`)}
                  >
                    <TableCell>
                      {(() => {
                        const { name, id } = parsePatientDisplay(visit.patient.display);
                        return (
                          <div>
                            <p className="font-semibold text-[var(--clinic-ink)]">{name}</p>
                            {id && <p className="text-xs text-[hsl(var(--muted-foreground))] font-mono">{id}</p>}
                          </div>
                        );
                      })()}
                    </TableCell>
                    <TableCell className="text-xs">
                      {visit.visitType?.display ?? "—"}
                    </TableCell>
                    <TableCell className="text-xs text-[var(--clinic-slate)]">
                      {visit.location?.display ?? "—"}
                    </TableCell>
                    <TableCell className="text-xs">
                      {formatDate(visit.startDatetime, "datetime")}
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary" className="text-xs">
                        {(visit.encounters ?? []).length}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 text-xs text-[var(--clinic-coral)]"
                        onClick={(e) => {
                          e.stopPropagation();
                          setPendingEnd(visit);
                        }}
                      >
                        <XCircle size={12} className="mr-1" /> End
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>

            {currentVisits.length > PREVIEW_LIMIT && (
              <div className="border-t border-emerald-100 px-4 py-2 flex items-center justify-between">
                <span className="text-xs text-emerald-700">
                  {showAllActive
                    ? `Showing all ${currentVisits.length} active visits`
                    : `Showing ${PREVIEW_LIMIT} of ${currentVisits.length} active visits`}
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 text-xs text-emerald-700 hover:bg-emerald-100"
                  onClick={() => setShowAllActive((v) => !v)}
                >
                  {showAllActive ? (
                    <><ChevronUp size={13} className="mr-1" /> Show less</>
                  ) : (
                    <><ChevronDown size={13} className="mr-1" /> See {hiddenActiveCount} more</>
                  )}
                </Button>
              </div>
            )}
          </>
        )}
      </Card>

      {/* ── Previous Visits ── */}
      {!isLoading && olderVisits.length > 0 && (
        <Card className="border-blue-200 bg-blue-50 overflow-hidden">
          <CardHeader className="border-b border-blue-100 px-4 py-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-semibold text-blue-800 flex items-center gap-2">
                <span className="inline-flex items-center justify-center size-4 rounded-full bg-blue-100">
                  <History size={10} className="text-blue-600" />
                </span>
                Previous Visits
              </CardTitle>
              <span className="text-xs text-blue-700 font-medium">
                {olderVisits.length} record{olderVisits.length !== 1 ? "s" : ""}
              </span>
            </div>
            <p className="text-xs text-blue-700/70 mt-0.5">Closed visits and imported visit history.</p>
          </CardHeader>

          <CardContent className="p-3 space-y-1.5">
            {visiblePrevious.map((visit) => (
              <button
                key={visit.uuid}
                  className="grid w-full grid-cols-[1fr_auto] gap-3 rounded-lg border border-blue-100 px-3 py-2 text-left hover:bg-blue-100/60 transition-colors"
                onClick={() => navigate(`/patients/${visit.patient.uuid}`)}
              >
                <span>
                  {(() => {
                    const { name, id } = parsePatientDisplay(visit.patient.display);
                    return (
                      <>
                        <span className="block text-sm font-semibold text-[var(--clinic-ink)]">{name}</span>
                        {id && <span className="block text-xs font-mono text-[hsl(var(--muted-foreground))]">{id}</span>}
                      </>
                    );
                  })()}
                  <span className="block text-xs text-[hsl(var(--muted-foreground))] mt-0.5">
                    {visit.visitType?.display ?? "—"} · {visit.location?.display ?? "—"}
                  </span>
                </span>
                <span className="text-right">
                  <span className="block text-xs text-[hsl(var(--muted-foreground))]">
                    {formatDate(visit.startDatetime, "datetime")}
                  </span>
                  <Badge
                    variant={isOpenVisit(visit) ? "warning" : "secondary"}
                    className="mt-1 text-xs"
                  >
                    {isOpenVisit(visit) ? "Open record" : "Completed"}
                  </Badge>
                </span>
              </button>
            ))}
          </CardContent>

          {olderVisits.length > PREVIEW_LIMIT && (
            <div className="border-t border-blue-100 px-4 py-2 flex items-center justify-between bg-blue-50/30">
              <span className="text-xs text-blue-700">
                {showAllPrevious
                  ? `Showing all ${olderVisits.length} previous visits`
                  : `Showing ${PREVIEW_LIMIT} of ${olderVisits.length} previous visits`}
              </span>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs text-blue-700 hover:bg-blue-100"
                onClick={() => setShowAllPrevious((v) => !v)}
              >
                {showAllPrevious ? (
                  <><ChevronUp size={13} className="mr-1" /> Show less</>
                ) : (
                  <><ChevronDown size={13} className="mr-1" /> See {hiddenPreviousCount} more</>
                )}
              </Button>
            </div>
          )}
        </Card>
      )}

      {/* ── End visit confirmation ── */}
      <AlertDialog open={Boolean(pendingEnd)} onOpenChange={(open) => !open && setPendingEnd(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>End {pendingEnd?.patient.display}'s visit?</AlertDialogTitle>
            <AlertDialogDescription>
              Ending this visit stops new documentation from being attached to it. Existing
              encounters, vitals, and orders are preserved.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => setPendingEnd(null)}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (pendingEnd) {
                  const startMs = new Date(pendingEnd.startDatetime).getTime();
                  const stopMs = Math.max(Date.now(), startMs + 1000);
                  endVisit.mutate({
                    uuid: pendingEnd.uuid,
                    patientUuid: pendingEnd.patient.uuid,
                    stopDatetime: new Date(stopMs).toISOString(),
                  });
                }
                setPendingEnd(null);
              }}
            >
              End visit
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
