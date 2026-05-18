import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";
import { differenceInMonths, differenceInYears, format, isValid, parseISO } from "date-fns";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

function parseDate(input: string | Date | undefined): Date | null {
  if (!input) return null;
  const date = typeof input === "string" ? parseISO(input) : input;
  return isValid(date) ? date : null;
}

export function formatDate(date: string | Date | undefined, style: "medium" | "short" | "time" | "datetime" = "medium"): string {
  const parsed = parseDate(date);
  if (!parsed) return "—";
  if (style === "short") return format(parsed, "P");
  if (style === "time") return format(parsed, "p");
  if (style === "datetime") return format(parsed, "P p");
  return format(parsed, "PPP");
}

/**
 * Returns a clinically meaningful age string. Uses local-time math so the
 * birthdate "1990-12-10" is not silently shifted by a day on Pacific clocks.
 * Newborns under one year are reported in months.
 */
export function calculateAge(birthdate: string | Date | undefined): string {
  const parsed = parseDate(birthdate);
  if (!parsed) return "—";
  const now = new Date();
  const years = differenceInYears(now, parsed);
  if (years >= 1) return `${years}y`;
  const months = differenceInMonths(now, parsed);
  if (months >= 1) return `${months}mo`;
  return "<1mo";
}

export function formatWaitTime(minutes: number): string {
  if (minutes < 60) return `${minutes}m`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

export function getInitials(name: string): string {
  if (!name) return "?";
  return name
    .trim()
    .split(/\s+/)
    .map((part) => part[0] ?? "")
    .join("")
    .toUpperCase()
    .slice(0, 2) || "?";
}
