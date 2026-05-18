import { cva } from "class-variance-authority";

export const badgeVariants = cva(
  "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors",
  {
    variants: {
      variant: {
        default: "border-transparent bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))]",
        secondary: "border-transparent bg-[hsl(var(--secondary))] text-[hsl(var(--secondary-foreground))]",
        outline: "text-[hsl(var(--foreground))]",
        success: "border-transparent bg-[#dff6f3] text-[#0f6e8c]",
        warning: "border-transparent bg-amber-100 text-amber-800",
        destructive: "border-transparent bg-[#ffe0db] text-[#9f2f20]",
        info: "border-transparent bg-[#f4fbfe] text-[#0f6e8c]",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);
