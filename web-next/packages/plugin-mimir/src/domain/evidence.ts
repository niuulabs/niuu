/**
 * Evidence domain — how well-proven a written fact is, and what a page links to.
 *
 * A resident writes facts; every timeline entry or raw source that bears on a
 * fact is a *proof*. The backend counts those proofs (`GET /evidence`) and
 * derives a trend from how fresh the newest one is. Nothing here composes or
 * summarises: a fact is shown exactly as it was written.
 */

/**
 * How a fact is holding up.
 *
 * - `new` — written, nothing supports it yet
 * - `strengthening` — fresh support, several proofs
 * - `stable` — fresh support
 * - `weakening` — the newest proof is getting old
 * - `stale` — the newest proof is old enough to distrust
 */
export type EvidenceTrend = 'new' | 'strengthening' | 'stable' | 'weakening' | 'stale';

/** One Key Fact on a page, with its proof count and trend. */
export interface FactEvidence {
  /** The fact, verbatim as written on the page. */
  fact: string;
  /** Timeline proofs + source proofs. */
  proofCount: number;
  trend: EvidenceTrend;
  /** Date of the newest timeline proof, or null when nothing supports the fact. */
  latestSupport: string | null;
  /** Dates of every supporting timeline entry. */
  supportingDates: string[];
  /** How many of the proofs come from raw ingested sources rather than the timeline. */
  sourceProofCount: number;
}

/** A page reached from another page by walking the link graph. */
export interface RelatedPage {
  path: string;
  /** Hops from the origin page (1 = direct neighbour). */
  hop: number;
  /** Typed edge label, or null for a plain wikilink. */
  rel: string | null;
  direction: 'in' | 'out';
}

/** A belief revision: rewrite one fact and append the transition to the timeline. */
export interface ReviseRequest {
  path: string;
  /** The fact as it stands today — must match the page verbatim. */
  oldFact: string;
  newFact: string;
  /** Who is revising, recorded in the timeline entry. */
  attribution: string;
}

/** Trends that mean a human should look at the fact again. */
const WEAK_TRENDS: readonly EvidenceTrend[] = ['weakening', 'stale'];

/** True when the fact's newest proof is old enough that it deserves a look. */
export function isWeakening(evidence: FactEvidence): boolean {
  return WEAK_TRENDS.includes(evidence.trend);
}

/** The evidence row for a fact, matched verbatim. Null when nothing matches. */
export function evidenceForFact(rows: FactEvidence[], fact: string): FactEvidence | null {
  return rows.find((row) => row.fact === fact) ?? null;
}
