import { describe, expect, it } from "vitest";
import {
  isCurrentActiveVisit,
  isOpenVisit,
  isStaleOpenVisit,
  sortVisitsNewestFirst,
} from "./visitStatus";

const now = new Date("2026-05-09T20:00:00Z");

describe("isOpenVisit", () => {
  it("is true when stopDatetime is missing", () => {
    expect(isOpenVisit({ startDatetime: now.toISOString() })).toBe(true);
  });
  it("is false when stopDatetime is set", () => {
    expect(
      isOpenVisit({ startDatetime: now.toISOString(), stopDatetime: now.toISOString() }),
    ).toBe(false);
  });
});

describe("isCurrentActiveVisit", () => {
  it("returns true for an open visit started in the past", () => {
    const startedAt = new Date(now.getTime() - 1000 * 60 * 60 * 48).toISOString();
    expect(isCurrentActiveVisit({ startDatetime: startedAt })).toBe(true);
  });
  it("returns true for an open visit with a future startDatetime (clock skew / pre-opened)", () => {
    // A visit with no stopDatetime is active regardless of startDatetime —
    // this covers timezone mis-parse and pre-scheduled visits.
    const future = new Date(now.getTime() + 1000 * 60 * 60 * 7).toISOString();
    expect(isCurrentActiveVisit({ startDatetime: future })).toBe(true);
  });
  it("returns false for a closed visit", () => {
    expect(
      isCurrentActiveVisit({
        startDatetime: now.toISOString(),
        stopDatetime: now.toISOString(),
      }),
    ).toBe(false);
  });
});

describe("isStaleOpenVisit", () => {
  it("flags open visits older than the configured max age", () => {
    const old = new Date(now.getTime() - 1000 * 60 * 60 * 72).toISOString();
    expect(isStaleOpenVisit({ startDatetime: old }, now)).toBe(true);
  });
  it("does not flag fresh open visits", () => {
    const fresh = new Date(now.getTime() - 1000 * 60 * 60).toISOString();
    expect(isStaleOpenVisit({ startDatetime: fresh }, now)).toBe(false);
  });
});

describe("sortVisitsNewestFirst", () => {
  it("returns a new array sorted by descending startDatetime", () => {
    const a = { startDatetime: "2026-01-01T00:00:00Z" };
    const b = { startDatetime: "2026-05-01T00:00:00Z" };
    const c = { startDatetime: "2026-03-01T00:00:00Z" };
    const sorted = sortVisitsNewestFirst([a, b, c]);
    expect(sorted).toEqual([b, c, a]);
  });
});
