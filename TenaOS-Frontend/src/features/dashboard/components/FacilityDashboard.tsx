import {
  Activity,
  ArrowRight,
  Clock,
  FlaskConical,
  ListOrdered,
  UserPlus,
  Users,
} from "lucide-react";
import { Link } from "react-router-dom";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useRecentPatients } from "@/features/patients/hooks/usePatients";
import { useActiveVisits } from "@/features/visits/hooks/useVisits";
import {
  useActiveVisitCount,
  usePendingLabOrderCount,
  useQueueWaitingCount,
} from "./useDashboardStats";
import { useAllQueueEntries } from "@/features/queues/hooks/useQueues";
import { useRecentLabOrders } from "@/features/lab/hooks/useLab";
import { openmrsRuntimeConfig } from "@/lib/openmrs/runtimeConfig";
import { isCurrentActiveVisit, isStaleOpenVisit } from "@/features/visits/utils/visitStatus";
import { calculateAge, getInitials } from "@/lib/utils";
import { useAuthStore } from "@/stores/authStore";
import { cn } from "@/lib/utils";

/** OpenMRS display strings are often "{ID} - {Full Name}". Split them apart. */
function splitDisplay(display: string): { id: string; name: string } {
  const idx = display.indexOf(" - ");
  if (idx === -1) return { id: "", name: display };
  return { id: display.slice(0, idx), name: display.slice(idx + 3) };
}

export function FacilityDashboard() {
  const { user } = useAuthStore();
  const queuesEnabled = openmrsRuntimeConfig.capabilities.queues;

  const { data: recentPatients, isLoading: loadingPatients } = useRecentPatients(5);
  const { data: visits, isLoading: loadingVisits } = useActiveVisits();
  const { data: activeVisitCount, isLoading: loadingCount } = useActiveVisitCount();
  const { data: pendingLabCount, isLoading: loadingLabs } = usePendingLabOrderCount();
  const { data: queueResult, isLoading: loadingQueue } = useQueueWaitingCount();
  const { data: queueEntries, isLoading: loadingQueueEntries } = useAllQueueEntries(
    undefined,
    queuesEnabled,
  );
  const { data: recentLabOrders, isLoading: loadingLabOrders } = useRecentLabOrders(5);

  const hour = new Date().getHours();
  const greeting =
    hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";

  const now = new Date();
  const allOpenVisits = (visits ?? []).filter((v) => isCurrentActiveVisit(v) || isStaleOpenVisit(v, now));
  const totalOpen = activeVisitCount ?? allOpenVisits.length;

  const waitingEntries = (queueEntries ?? []).filter((e) => !e.endedAt);
  const visibleVisits = allOpenVisits.slice(0, 5);
  const visibleQueue = waitingEntries.slice(0, 5);

  return (
    <div className="space-y-6">
      {/* Welcome row */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-[var(--clinic-ink)]">
            {greeting},{" "}
            {user?.display?.split(" ")[0] || user?.username || "Clinician"}
          </h1>
          <p className="text-sm text-[hsl(var(--muted-foreground))] mt-0.5">
            {new Date().toLocaleDateString(undefined, {
              weekday: "long",
              year: "numeric",
              month: "long",
              day: "numeric",
            })}
          </p>
        </div>
        <Link
          to="/patients/register"
          className="hidden sm:inline-flex items-center gap-1.5 rounded-xl bg-[hsl(var(--primary))] text-white text-sm font-medium px-4 py-2 hover:opacity-90 transition-opacity"
        >
          <UserPlus size={15} />
          Register patient
        </Link>
      </div>

      {/* Stat cards — 4 canonical OpenMRS 3 OPD metrics */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          icon={Activity}
          label="Active visits"
          value={loadingCount || loadingVisits ? null : String(totalOpen)}
          sub="in clinic now"
          iconClass="bg-emerald-100 text-emerald-700"
          cardClass="bg-emerald-50 border-emerald-200"
          href="/visits"
          accent={totalOpen > 0}
        />
        <StatCard
          icon={ListOrdered}
          label="Patients in queue"
          value={
            loadingQueue
              ? null
              : queueResult?.status === "ok"
              ? String(queueResult.count)
              : undefined
          }
          sub={
            queueResult?.status === "disabled"
              ? "Queues not enabled"
              : queueResult?.status === "unavailable"
              ? "Module not installed"
              : "waiting now"
          }
          iconClass="bg-amber-100 text-amber-700"
          cardClass="bg-amber-50 border-amber-200"
          href="/queues"
          accent={queueResult?.status === "ok" && queueResult.count > 0}
          muted={queueResult?.status !== "ok"}
        />
        <StatCard
          icon={FlaskConical}
          label="Pending labs"
          value={loadingLabs ? null : String(pendingLabCount ?? 0)}
          sub="orders awaiting"
          iconClass="bg-purple-100 text-purple-700"
          cardClass="bg-purple-50 border-purple-200"
          href="/labs"
          accent={(pendingLabCount ?? 0) > 0}
        />
        <StatCard
          icon={Users}
          label="Recent patients"
          value={loadingPatients ? null : String(recentPatients?.results?.length ?? 0)}
          sub="loaded in list"
          iconClass="bg-blue-100 text-blue-700"
          cardClass="bg-blue-50 border-blue-200"
          href="/patients"
        />
      </div>

      {/* Main content — 3-panel layout */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 lg:h-[680px]">

        {/* Left column: Active visits + Patients in queue stacked */}
        <div className="lg:col-span-2 flex flex-col gap-4 h-full">

          {/* Active visits — top 5 */}
          <Card className="flex flex-col flex-1 min-h-0 border-emerald-200 bg-emerald-50 overflow-hidden">
            <CardHeader className="pb-3 shrink-0 border-b border-emerald-100">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm flex items-center gap-2 text-emerald-800">
                  <span className="relative flex size-2">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                    <span className="relative inline-flex rounded-full size-2 bg-emerald-500" />
                  </span>
                  Active visits
                </CardTitle>
                <Link
                  to="/visits"
                  className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 transition-colors"
                >
                  View all <ArrowRight size={12} />
                </Link>
              </div>
            </CardHeader>
            <CardContent className="flex-1 overflow-y-auto p-0">
              {loadingVisits ? (
                <div className="divide-y divide-emerald-100">
                  {Array(3).fill(0).map((_, i) => (
                    <div key={i} className="flex items-center gap-3 px-4 py-3">
                      <Skeleton className="h-8 w-8 rounded-full shrink-0" />
                      <div className="flex-1 space-y-1.5">
                        <Skeleton className="h-3 w-36 rounded" />
                        <Skeleton className="h-2.5 w-24 rounded" />
                      </div>
                      <Skeleton className="h-5 w-16 rounded-full" />
                    </div>
                  ))}
                </div>
              ) : allOpenVisits.length === 0 ? (
                <div className="py-10 text-center text-sm text-[hsl(var(--muted-foreground))]">
                  No open visits at this time.{" "}
                  <Link to="/patients" className="text-[var(--clinic-blue)] hover:underline">
                    Find a patient
                  </Link>{" "}
                  and start a visit.
                </div>
              ) : (
                <>
                  <div className="divide-y divide-emerald-100">
                    {visibleVisits.map((visit) => {
                      const duration = Math.round(
                        (now.getTime() - new Date(visit.startDatetime).getTime()) / 60_000,
                      );
                      const durationLabel =
                        duration < 60
                          ? `${duration}m`
                          : duration < 1440
                          ? `${Math.floor(duration / 60)}h ${duration % 60}m`
                          : `${Math.floor(duration / 1440)}d`;
                      const isLong = duration >= 1440;
                      const { id: patientId, name: patientName } = splitDisplay(visit.patient.display);

                      return (
                        <Link
                          key={visit.uuid}
                          to={`/patients/${visit.patient.uuid}`}
                          className="flex items-center gap-3 px-4 py-3 hover:bg-emerald-100/60 transition-colors"
                        >
                          <div className="h-9 w-9 rounded-full bg-[var(--clinic-mint)] text-[var(--clinic-blue)] flex items-center justify-center text-xs font-semibold shrink-0">
                            {getInitials(patientName)}
                          </div>
                          <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium text-[var(--clinic-ink)] truncate">
                              {patientName}
                            </p>
                            <p className="text-xs text-[hsl(var(--muted-foreground))] truncate">
                              {visit.visitType?.display ?? "—"} · {visit.location?.display ?? "—"}
                              {patientId && <span className="font-mono text-[var(--clinic-slate)]"> · {patientId}</span>}
                            </p>
                          </div>
                          <div className="flex items-center gap-2 shrink-0">
                            <span className={cn(
                              "text-xs flex items-center gap-1",
                              isLong ? "text-amber-600" : "text-[var(--clinic-slate)]",
                            )}>
                              <Clock size={11} />
                              {durationLabel}
                            </span>
                            <Badge variant="success" className="text-xs">Active</Badge>
                          </div>
                        </Link>
                      );
                    })}
                  </div>
                </>
              )}
            </CardContent>
          </Card>

          {/* Patients in queue — top 5 */}
          <Card className="flex flex-col flex-1 min-h-0 border-amber-200 bg-amber-50 overflow-hidden">
            <CardHeader className="pb-3 shrink-0 border-b border-amber-100">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm flex items-center gap-2 text-amber-800">
                  <ListOrdered size={13} className="text-amber-500" />
                  Patients in queue
                </CardTitle>
                <Link
                  to="/queues"
                  className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg bg-amber-600 text-white hover:bg-amber-700 transition-colors"
                >
                  View all <ArrowRight size={12} />
                </Link>
              </div>
            </CardHeader>
            <CardContent className="flex-1 overflow-y-auto p-0">
              {!queuesEnabled ? (
                <div className="py-8 text-center text-xs text-[hsl(var(--muted-foreground))] italic px-4">
                  Queue module is not enabled for this facility.
                </div>
              ) : loadingQueueEntries ? (
                <div className="divide-y divide-amber-100">
                  {Array(4).fill(0).map((_, i) => (
                    <div key={i} className="flex items-center gap-3 px-4 py-3">
                      <Skeleton className="h-8 w-8 rounded-full shrink-0" />
                      <div className="flex-1 space-y-1.5">
                        <Skeleton className="h-3 w-32 rounded" />
                        <Skeleton className="h-2.5 w-20 rounded" />
                      </div>
                      <Skeleton className="h-5 w-14 rounded-full" />
                    </div>
                  ))}
                </div>
              ) : waitingEntries.length === 0 ? (
                <div className="py-10 text-center text-sm text-[hsl(var(--muted-foreground))]">
                  No patients currently in queue.
                </div>
              ) : (
                <>
                  <div className="divide-y divide-amber-100">
                    {visibleQueue.map((entry, idx) => {
                      const { id: qPatientId, name: qPatientName } = splitDisplay(entry.patient?.display ?? "");
                      return (
                        <Link
                          key={entry.uuid}
                          to={`/patients/${entry.patient.uuid}`}
                          className="flex items-center gap-3 px-4 py-3 hover:bg-amber-100/60 transition-colors"
                        >
                          <div className="h-6 w-6 rounded-full bg-amber-50 text-amber-600 flex items-center justify-center text-xs font-bold shrink-0">
                            {idx + 1}
                          </div>
                          <div className="h-8 w-8 rounded-full bg-[var(--clinic-mint)] text-[var(--clinic-blue)] flex items-center justify-center text-xs font-semibold shrink-0">
                            {getInitials(qPatientName)}
                          </div>
                          <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium text-[var(--clinic-ink)] truncate">
                              {qPatientName}
                            </p>
                            <p className="text-xs text-[hsl(var(--muted-foreground))] truncate">
                              {entry.queue?.name} · {entry.status?.display}
                              {qPatientId && <span className="font-mono text-[var(--clinic-slate)]"> · {qPatientId}</span>}
                            </p>
                          </div>
                          <div className="shrink-0">
                            <Badge
                              variant="outline"
                              className={cn(
                                "text-xs",
                                entry.priority?.display === "Emergency"
                                  ? "border-red-300 text-red-600"
                                  : entry.priority?.display === "Urgent"
                                  ? "border-amber-300 text-amber-600"
                                  : "border-[var(--clinic-border)] text-[var(--clinic-slate)]",
                              )}
                            >
                              {entry.priority?.display ?? "Normal"}
                            </Badge>
                          </div>
                        </Link>
                      );
                    })}
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Right column: Recent patients + Recent lab orders */}
        <div className="flex flex-col gap-4 h-full">
        <Card className="flex flex-col flex-1 min-h-0 border-blue-200 bg-blue-50 overflow-hidden">
          <CardHeader className="pb-3 shrink-0 border-b border-blue-100">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm text-blue-800">Recent patients</CardTitle>
              <Link
                to="/patients"
                className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors"
              >
                View all <ArrowRight size={12} />
              </Link>
            </div>
          </CardHeader>
          <CardContent className="flex-1 overflow-y-auto p-0">
            {!loadingPatients && (recentPatients?.results?.length ?? 0) === 0 && (
              <div className="px-4 pb-4">
                <p className="text-xs text-[hsl(var(--muted-foreground))] text-center py-8">
                  No patients loaded.{" "}
                  <Link
                    to="/patients/register"
                    className="text-[var(--clinic-blue)] hover:underline"
                  >
                    Register the first patient.
                  </Link>
                </p>
              </div>
            )}
            <div className="divide-y divide-blue-100">
              {loadingPatients
                ? Array(5).fill(0).map((_, i) => (
                    <div key={i} className="flex items-center gap-3 px-4 py-3">
                      <Skeleton className="h-8 w-8 rounded-full shrink-0" />
                      <div className="flex-1 space-y-1.5">
                        <Skeleton className="h-3 w-28 rounded" />
                        <Skeleton className="h-2.5 w-20 rounded" />
                      </div>
                    </div>
                  ))
                : (recentPatients?.results ?? []).slice(0, 5).map((patient) => (
                    <Link
                      key={patient.uuid}
                      to={`/patients/${patient.uuid}`}
                      className="flex items-center gap-3 px-4 py-3 hover:bg-blue-100/60 transition-colors"
                    >
                      <div
                        className={cn(
                          "h-8 w-8 rounded-full flex items-center justify-center text-xs font-semibold shrink-0",
                          patient.person.dead
                            ? "bg-[hsl(var(--muted))] text-[var(--clinic-slate)]"
                            : "bg-[var(--clinic-mint)] text-[var(--clinic-blue)]",
                        )}
                      >
                        {getInitials(patient.person.display)}
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-[var(--clinic-ink)] truncate">
                          {patient.person.display}
                        </p>
                        <p className="text-xs text-[hsl(var(--muted-foreground))]">
                          {calculateAge(patient.person.birthdate)} ·{" "}
                          {{ M: "Male", F: "Female", O: "Other", U: "Unknown" }[patient.person.gender] ?? patient.person.gender}
                        </p>
                      </div>
                      <div className="shrink-0">
                        <p className="text-xs text-[var(--clinic-slate)] font-mono">
                          {patient.identifiers[0]?.identifier ?? ""}
                        </p>
                        {patient.person.dead && (
                          <Badge variant="destructive" className="text-xs mt-0.5">
                            Deceased
                          </Badge>
                        )}
                      </div>
                    </Link>
                  ))}
            </div>
          </CardContent>
        </Card>

        {/* Recent lab orders */}
        <Card className="flex flex-col flex-1 min-h-0 border-purple-200 bg-purple-50 overflow-hidden">
          <CardHeader className="pb-3 shrink-0 border-b border-purple-100">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm flex items-center gap-2 text-purple-800">
                <FlaskConical size={13} className="text-purple-500" />
                Recent lab orders
              </CardTitle>
              <Link
                to="/labs"
                className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg bg-purple-600 text-white hover:bg-purple-700 transition-colors"
              >
                View all <ArrowRight size={12} />
              </Link>
            </div>
          </CardHeader>
          <CardContent className="flex-1 overflow-y-auto p-0">
            {loadingLabOrders ? (
              <div className="divide-y divide-[var(--clinic-border)]">
                {Array(4).fill(0).map((_, i) => (
                  <div key={i} className="flex items-center gap-3 px-4 py-3">
                    <Skeleton className="h-7 w-7 rounded-lg shrink-0" />
                    <div className="flex-1 space-y-1.5">
                      <Skeleton className="h-3 w-28 rounded" />
                      <Skeleton className="h-2.5 w-20 rounded" />
                    </div>
                    <Skeleton className="h-5 w-16 rounded-full" />
                  </div>
                ))}
              </div>
            ) : (recentLabOrders ?? []).length === 0 ? (
              <div className="py-8 text-center text-xs text-[hsl(var(--muted-foreground))]">
                No lab orders found.
              </div>
            ) : (
              <div className="divide-y divide-purple-100">
                {(recentLabOrders ?? []).map((order) => {
                  const { id: labPatientId, name: labPatientName } = splitDisplay(order.patient.display);
                  const status = order.fulfillerStatus ?? "ORDERED";
                  const statusMeta: Record<string, { label: string; cls: string }> = {
                    ORDERED:      { label: "Ordered",     cls: "border-[var(--clinic-border)] text-[var(--clinic-slate)]" },
                    RECEIVED:     { label: "Received",    cls: "border-blue-200 text-blue-600" },
                    IN_PROGRESS:  { label: "In progress", cls: "border-amber-200 text-amber-600" },
                    COMPLETED:    { label: "Completed",   cls: "border-emerald-200 text-emerald-600" },
                    EXCEPTION:    { label: "Exception",   cls: "border-red-200 text-red-600" },
                  };
                  const sm = statusMeta[status] ?? statusMeta["ORDERED"];
                  return (
                    <Link
                      key={order.uuid}
                      to={`/patients/${order.patient.uuid}`}
                      className="flex items-center gap-3 px-4 py-3 hover:bg-purple-100/60 transition-colors"
                    >
                      <div className="h-7 w-7 rounded-lg bg-purple-50 text-purple-600 flex items-center justify-center shrink-0">
                        <FlaskConical size={13} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-[var(--clinic-ink)] truncate">
                          {order.concept.display}
                        </p>
                        <p className="text-xs text-[hsl(var(--muted-foreground))] truncate">
                          {labPatientName}
                          {labPatientId && <span className="font-mono text-[var(--clinic-slate)]"> · {labPatientId}</span>}
                        </p>
                      </div>
                      <div className="shrink-0 text-right">
                        <Badge variant="outline" className={cn("text-xs", sm.cls)}>
                          {sm.label}
                        </Badge>
                        <p className="text-xs text-[var(--clinic-slate)] mt-0.5">
                          {new Date(order.dateActivated).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                        </p>
                      </div>
                    </Link>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>
        </div>
      </div>
    </div>
  );
}

interface StatCardProps {
  icon: React.ComponentType<{ size?: number; className?: string }>;
  label: string;
  /**
   * Numeric string to display.
   * - `null`      → loading skeleton
   * - `undefined` → no number; show only the `sub` label (module off / unavailable)
   * - `string`    → rendered as a large bold number
   */
  value: string | null | undefined;
  sub: string;
  iconClass: string;
  cardClass: string;
  href: string;
  accent?: boolean;
  /** When true, renders the card in a muted/secondary style (module not available). */
  muted?: boolean;
}

function StatCard({ icon: Icon, label, value, sub, iconClass, cardClass, href, muted }: StatCardProps) {
  return (
    <Link to={href}>
      <Card
        className={cn(
          "hover:shadow-md transition-all cursor-pointer h-full",
          cardClass,
          muted && "opacity-60",
        )}
      >
        <CardContent className="p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-xs font-medium text-[hsl(var(--muted-foreground))] leading-none mb-1.5">
                {label}
              </p>
              {value === null ? (
                <Skeleton className="h-7 w-12 rounded" />
              ) : value === undefined ? (
                <p className="text-xs text-[hsl(var(--muted-foreground))] italic mt-1">{sub}</p>
              ) : (
                <>
                  <p className="text-2xl font-bold text-[var(--clinic-ink)] leading-none">
                    {value}
                  </p>
                  <p className="text-xs text-[var(--clinic-slate)] mt-1">{sub}</p>
                </>
              )}
            </div>
            <div
              className={cn(
                "h-10 w-10 rounded-xl flex items-center justify-center shrink-0",
                iconClass,
              )}
            >
              <Icon size={20} />
            </div>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}

