import * as React from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";

interface WorkspaceProps {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  headerAction?: React.ReactNode;
  footer?: React.ReactNode;
  className?: string;
  wide?: boolean;
}

export function Workspace({
  open,
  onClose,
  title,
  subtitle,
  children,
  headerAction,
  footer,
  className,
  wide = false,
}: WorkspaceProps) {
  if (!open) return null;

  return (
    <>
      <div
        className="fixed inset-0 z-40 bg-[#061824]/20 backdrop-blur-[2px] lg:hidden"
        onClick={onClose}
      />
      <aside
        className={cn(
          "fixed inset-y-0 right-0 z-50 flex flex-col border-l bg-white shadow-xl transition-transform",
          wide ? "w-full max-w-3xl" : "w-full max-w-xl",
          className,
        )}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--clinic-border)]">
          <div>
            <h2 className="text-base font-semibold text-[var(--clinic-ink)]">{title}</h2>
            {subtitle && (
              <p className="text-sm text-[hsl(var(--muted-foreground))] mt-0.5">{subtitle}</p>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {headerAction}
            <Button variant="ghost" size="icon" onClick={onClose} className="shrink-0">
              <X className="size-4" />
              <span className="sr-only">Close</span>
            </Button>
          </div>
        </div>

        {/* Body */}
        <ScrollArea className="flex-1">
          <div className="p-5">{children}</div>
        </ScrollArea>

        {/* Footer */}
        {footer && (
          <>
            <Separator />
            <div className="flex items-center justify-end gap-2 px-5 py-4">
              {footer}
            </div>
          </>
        )}
      </aside>
    </>
  );
}
