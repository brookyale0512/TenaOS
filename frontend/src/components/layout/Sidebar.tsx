import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import {
  LayoutDashboard,
  Users,
  ClipboardList,
  ListOrdered,
  FileText,
  FlaskConical,
  Stethoscope,
  PanelLeft,
  PanelRight,
  CalendarDays,
  BarChart3,
  ChevronDown,
  ChevronRight,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useUiStore } from "@/stores/uiStore";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Button } from "@/components/ui/button";
import { openmrsRuntimeConfig } from "@/lib/openmrs/runtimeConfig";
import { LocationSelector } from "./LocationSelector";
import { usePublishedReportList } from "@/features/reports/hooks/useReportBuilder";

const navGroups = [
  {
    label: "Clinic Operations",
    items: [
      { path: "/", icon: LayoutDashboard, label: "Dashboard" },
      { path: "/visits", icon: Stethoscope, label: "Clinical Visits" },
      ...(openmrsRuntimeConfig.capabilities.queues ? [{ path: "/queues", icon: ListOrdered, label: "Queues" }] : []),
      ...(openmrsRuntimeConfig.capabilities.appointments ? [{ path: "/appointments", icon: CalendarDays, label: "Appointments" }] : []),
    ],
  },
  {
    label: "Patient Care",
    items: [
      { path: "/patients", icon: Users, label: "Patients" },
      { path: "/labs", icon: FlaskConical, label: "Lab Tests", exact: true },
      { path: "/reports", icon: BarChart3, label: "Reports", exact: true, children: "reports" },
    ],
  },
  {
    label: "Administration",
    items: [
      { path: "/forms", icon: ClipboardList, label: "Manage Forms" },
      { path: "/reports/manage", icon: FileText, label: "Manage Reports" },
      { path: "/labs/manage", icon: FlaskConical, label: "Manage Lab Tests" },
    ],
  },
];

export function Sidebar() {
  const { sidebarOpen, toggleSidebar } = useUiStore();
  const location = useLocation();
  const publishedReports = usePublishedReportList();
  const [reportsOpen, setReportsOpen] = useState(false);

  return (
    <aside className={cn("sticky top-0 hidden h-svh shrink-0 overflow-visible border-r bg-white/90 backdrop-blur-xl transition-all duration-200 lg:flex lg:flex-col", sidebarOpen ? "w-60" : "w-16")}>
      <div className={cn("relative flex h-16 items-center border-b border-[var(--clinic-border)] px-3", sidebarOpen ? "justify-between" : "justify-center")}>
        <div className="flex min-w-0 items-center gap-3 shrink-0">
          <div className="w-9 h-9 rounded-xl bg-[var(--clinic-teal)] flex items-center justify-center shrink-0">
            <span className="text-white text-sm font-bold">T</span>
          </div>
          {sidebarOpen && <span className="font-semibold text-[var(--clinic-ink)] text-base whitespace-nowrap">TenaOS</span>}
        </div>
        {sidebarOpen ? (
          <Button type="button" variant="ghost" size="icon" className="size-9 shrink-0" onClick={toggleSidebar}>
            <PanelLeft className="size-4" />
            <span className="sr-only">Collapse sidebar</span>
          </Button>
        ) : (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button type="button" variant="secondary" size="icon" className="absolute -right-3 top-5 z-20 size-7 rounded-full border bg-white p-0 shadow-sm" onClick={toggleSidebar}>
                <PanelRight className="size-3.5" />
                <span className="sr-only">Expand sidebar</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">Expand sidebar</TooltipContent>
          </Tooltip>
        )}
      </div>

      <nav className="flex-1 overflow-y-auto overflow-x-hidden px-2.5 pb-4 pt-4">
        <LocationSelector expanded={sidebarOpen} />
        {navGroups.map((group) => (
          <div key={group.label} className="mb-4">
            {sidebarOpen && <div className="px-3 py-2 text-xs font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">{group.label}</div>}
            <div className="grid gap-1">
              {group.items.map(({ path, icon: Icon, label, exact, children }) => {
                const active = (path === "/" || exact) ? location.pathname === path : location.pathname.startsWith(path);
                const reportChildren = children === "reports" ? (publishedReports.data ?? []) : [];
                const hasReportChildren = children === "reports";
                const item = (
                  <Link to={path} className={cn("flex w-full items-center gap-3 rounded-xl px-3 py-2 text-left text-sm font-medium text-[#0b2b3c] transition-colors hover:bg-[#f4fbfe]", active && "bg-[#dff6f3] text-[#0f6e8c] shadow-sm")}>
                    <Icon className="shrink-0 size-[18px]" />
                    {sidebarOpen && <span className="truncate">{label}</span>}
                  </Link>
                );
                const reportsItem = (
                  <div
                    className={cn(
                      "flex w-full items-center gap-2 rounded-xl text-sm font-medium text-[#0b2b3c] transition-colors hover:bg-[#f4fbfe]",
                      active && "bg-[#dff6f3] text-[#0f6e8c] shadow-sm",
                    )}
                  >
                    <Link to={path} className="flex min-w-0 flex-1 items-center gap-3 px-3 py-2">
                      <Icon className="shrink-0 size-[18px]" />
                      <span className="truncate">{label}</span>
                    </Link>
                    <button
                      type="button"
                      onClick={(event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        setReportsOpen((value) => !value);
                      }}
                      className="mr-1 flex size-7 shrink-0 items-center justify-center rounded-lg text-[var(--clinic-slate)] hover:bg-white hover:text-[var(--clinic-blue)]"
                      aria-label={reportsOpen ? "Collapse reports" : "Expand reports"}
                      aria-expanded={reportsOpen}
                    >
                      {reportsOpen ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                    </button>
                  </div>
                );
                return (
                  <div key={path}>
                    {sidebarOpen ? (
                      hasReportChildren ? reportsItem : item
                    ) : (
                      <Tooltip><TooltipTrigger asChild>{item}</TooltipTrigger><TooltipContent side="right">{label}</TooltipContent></Tooltip>
                    )}
                    {sidebarOpen && hasReportChildren && reportsOpen && (
                      <div className="ml-5 mt-1 rounded-2xl border border-[#d6f0ed] bg-[#f4fbfe] p-1.5">
                        {reportChildren.length > 0 ? (
                          <div className="grid gap-1">
                            {reportChildren.slice(0, 8).map((report) => (
                              <Link
                                key={report.draftId}
                                to={`/reports/view/${report.draftId}`}
                                className={cn(
                                  "group flex min-w-0 items-center gap-2 rounded-xl px-2.5 py-2 text-xs text-[#0b2b3c] transition-colors hover:bg-white",
                                  location.pathname === `/reports/view/${report.draftId}` && "bg-white text-[#0f6e8c] ring-1 ring-[#b2e8e2]",
                                )}
                                title={report.name}
                              >
                                <span className="size-1.5 shrink-0 rounded-full bg-[var(--clinic-teal)] opacity-70 group-hover:opacity-100" />
                                <span className="truncate">{report.name}</span>
                              </Link>
                            ))}
                          </div>
                        ) : (
                          <div className="px-2.5 py-2 text-xs text-[hsl(var(--muted-foreground))]">
                            No published reports
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </nav>
    </aside>
  );
}
