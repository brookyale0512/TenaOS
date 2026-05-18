import { Outlet, useLocation } from "react-router-dom";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Sidebar } from "./Sidebar";
import { Header } from "./Header";
import { ToastContainer } from "./ToastContainer";
import { cn } from "@/lib/utils";

export function AppShell() {
  const location = useLocation();
  const usesFixedWorkspace =
    location.pathname === "/forms/new" ||
    location.pathname === "/reports/new" ||
    /^\/reports\/(?!manage$|view\/)[^/]+$/.test(location.pathname);

  return (
    <TooltipProvider delayDuration={400}>
      <div className="fixed inset-0 flex overflow-hidden">
        <Sidebar />
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden min-w-0">
          <Header />
          <main
            className={cn(
              "min-h-0 flex-1 overscroll-contain",
              usesFixedWorkspace ? "h-full overflow-hidden p-0" : "overflow-y-auto p-4 md:p-6",
            )}
          >
            <Outlet />
          </main>
        </div>
      </div>
      <ToastContainer />
    </TooltipProvider>
  );
}
