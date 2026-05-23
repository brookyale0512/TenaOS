import { CheckCircle, AlertCircle, AlertTriangle, Info, X } from "lucide-react";
import { useUiStore } from "@/stores/uiStore";
import { cn } from "@/lib/utils";

const variantConfig = {
  default: { icon: Info, bg: "bg-[#f4fbfe]", border: "border-[#dff6f3]", text: "text-[#0b2b3c]", icon_color: "text-[#0f6e8c]" },
  success: { icon: CheckCircle, bg: "bg-emerald-50", border: "border-emerald-200", text: "text-emerald-800", icon_color: "text-emerald-600" },
  warning: { icon: AlertTriangle, bg: "bg-amber-50", border: "border-amber-200", text: "text-amber-800", icon_color: "text-amber-600" },
  destructive: { icon: AlertCircle, bg: "bg-[#fff2ef]", border: "border-[#ffd7d0]", text: "text-[#8d2b20]", icon_color: "text-[#9f2f20]" },
};

export function ToastContainer() {
  const { toasts, removeToast } = useUiStore();

  return (
    <div
      className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 w-80"
      role="region"
      aria-label="Notifications"
    >
      {toasts.map((toast) => {
        const config = variantConfig[toast.variant];
        const Icon = config.icon;
        const isAssertive = toast.variant === "destructive" || toast.variant === "warning";
        return (
          <div
            key={toast.id}
            role={isAssertive ? "alert" : "status"}
            aria-live={isAssertive ? "assertive" : "polite"}
            aria-atomic="true"
            className={cn(
              "flex items-start gap-3 p-3 rounded-xl border shadow-md",
              config.bg,
              config.border
            )}
          >
            <Icon size={16} className={cn("mt-0.5 shrink-0", config.icon_color)} />
            <div className="flex-1 min-w-0">
              <p className={cn("text-sm font-medium", config.text)}>{toast.title}</p>
              {toast.description && (
                <p className={cn("text-xs mt-0.5 opacity-80", config.text)}>{toast.description}</p>
              )}
            </div>
            <button
              onClick={() => removeToast(toast.id)}
              className={cn("shrink-0 opacity-60 hover:opacity-100 transition-opacity", config.text)}
            >
              <X size={14} />
            </button>
          </div>
        );
      })}
    </div>
  );
}
