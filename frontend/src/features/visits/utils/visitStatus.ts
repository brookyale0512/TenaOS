import { openmrsRuntimeConfig } from "@/lib/openmrs/runtimeConfig";

export interface VisitLike {
  startDatetime: string;
  stopDatetime?: string | null;
}

/**
 * Visit-state predicates.
 *
 * - `isOpenVisit`: visit has no `stopDatetime` (the canonical OpenMRS active
 *   predicate).
 * - `isCurrentActiveVisit`: visit is open AND was started in the past. We
 *   treat any open visit as "currently active" regardless of how long ago it
 *   was started; long-running open visits get flagged separately as
 *   `isStaleOpenVisit` so dashboards can surface them, but they remain
 *   `current` for the purposes of attaching new encounters.
 */
export function isOpenVisit(visit: VisitLike): boolean {
  return !visit.stopDatetime;
}

/**
 * A visit is "currently active" if it has no stopDatetime — exactly the same
 * definition OpenMRS itself uses.  We do NOT filter on startDatetime because:
 *   - Doctors can pre-open visits before the patient arrives.
 *   - Browser/server clock skew or datetime-local timezone quirks can make a
 *     freshly-created visit appear to have started slightly in the future.
 * The only true signal for "this visit is done" is a non-null stopDatetime.
 */
export function isCurrentActiveVisit(visit: VisitLike): boolean {
  return isOpenVisit(visit);
}

export function isStaleOpenVisit(visit: VisitLike, now = new Date()): boolean {
  if (!isOpenVisit(visit)) return false;
  const startedAt = new Date(visit.startDatetime);
  if (Number.isNaN(startedAt.getTime())) return false;
  const maxAgeMs = openmrsRuntimeConfig.activeVisitMaxAgeHours * 60 * 60 * 1000;
  return now.getTime() - startedAt.getTime() > maxAgeMs;
}

export function sortVisitsNewestFirst<T extends VisitLike>(visits: T[]): T[] {
  return visits.slice().sort((a, b) => new Date(b.startDatetime).getTime() - new Date(a.startDatetime).getTime());
}
