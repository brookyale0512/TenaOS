import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { calculateAge, cn, formatDate, formatWaitTime, getInitials } from "./utils";

describe("cn", () => {
  it("merges Tailwind class lists with twMerge semantics", () => {
    expect(cn("px-2", "px-4")).toBe("px-4");
    expect(cn("text-red-500", undefined, false, "font-bold")).toBe("text-red-500 font-bold");
  });
});

describe("formatDate", () => {
  it("returns an em dash for empty input", () => {
    expect(formatDate(undefined)).toBe("—");
    expect(formatDate("not-a-date")).toBe("—");
  });

  it("formats ISO dates locally without UTC offset shifts", () => {
    expect(formatDate("1990-12-10", "short")).not.toBe("—");
  });
});

describe("calculateAge", () => {
  const fixedNow = new Date("2026-05-09T12:00:00Z");
  beforeAll(() => {
    vi.useFakeTimers();
    vi.setSystemTime(fixedNow);
  });
  afterAll(() => {
    vi.useRealTimers();
  });

  it("reports years for adults", () => {
    expect(calculateAge("1990-05-09")).toBe("36y");
  });
  it("reports months for under-1-year", () => {
    expect(calculateAge("2026-01-09")).toBe("4mo");
  });
  it("reports <1mo for newborns", () => {
    expect(calculateAge("2026-05-01")).toBe("<1mo");
  });
  it("returns em dash for missing/invalid input", () => {
    expect(calculateAge(undefined)).toBe("—");
    expect(calculateAge("not-a-date")).toBe("—");
  });
});

describe("formatWaitTime", () => {
  it("renders minutes under an hour", () => {
    expect(formatWaitTime(45)).toBe("45m");
  });
  it("renders hours and minutes", () => {
    expect(formatWaitTime(125)).toBe("2h 5m");
    expect(formatWaitTime(120)).toBe("2h");
  });
});

describe("getInitials", () => {
  it("uses the first letter of up to two name parts", () => {
    expect(getInitials("Ada Lovelace")).toBe("AL");
    expect(getInitials("john")).toBe("J");
  });
  it("handles names with extra whitespace", () => {
    expect(getInitials("  Ada   Byron Lovelace  ")).toBe("AB");
  });
  it("falls back to ? for empty input", () => {
    expect(getInitials("")).toBe("?");
    expect(getInitials("   ")).toBe("?");
  });
});
