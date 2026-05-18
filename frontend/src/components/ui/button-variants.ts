import { cva } from "class-variance-authority";

export const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-xl text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))] disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4",
  {
    variants: {
      variant: {
        default: "bg-[hsl(var(--primary))] text-white shadow-sm hover:bg-[#0fa092]",
        secondary: "border bg-white text-[hsl(var(--foreground))] shadow-sm hover:bg-[hsl(var(--muted))]",
        outline: "border bg-transparent hover:bg-[hsl(var(--muted))]",
        ghost: "hover:bg-[hsl(var(--muted))]",
        destructive: "bg-[hsl(var(--destructive))] text-white hover:opacity-90",
        link: "text-[hsl(var(--accent))] underline-offset-4 hover:underline",
        success: "bg-[hsl(var(--clinical-success))] text-white hover:opacity-90 shadow-sm",
        warning: "bg-[hsl(var(--clinical-warning))] text-white hover:opacity-90 shadow-sm",
      },
      size: {
        default: "h-10 px-4 py-2",
        sm: "h-9 rounded-lg px-3",
        lg: "h-11 rounded-2xl px-6",
        icon: "size-10",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);
