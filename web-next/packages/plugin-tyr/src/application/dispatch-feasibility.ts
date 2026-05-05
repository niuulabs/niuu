/**
 * Feasibility engine — pre-dispatch gate checks for raids.
 *
 * All four gates must pass before the Dispatch button is enabled.
 * Surface failing gates as per-row warning chips, never disable silently.
 */

import type { Raid, Phase } from '../domain/saga';
import type { DispatcherState } from '../domain/dispatcher';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type FeasibilityGateName =
  | 'raven_resolution'
  | 'confidence'
  | 'upstream_blocked'
  | 'cluster_healthy';

export interface FeasibilityGate {
  name: FeasibilityGateName;
  passed: boolean;
  /** Human-readable reason surfaced as a tooltip chip in the UI. */
  reason: string;
}

export interface FeasibilityResult {
  feasible: boolean;
  gates: FeasibilityGate[];
}

export interface FeasibilityContext {
  raid: Raid;
  phase: Phase;
  /** All phases belonging to the same saga (for upstream-blocked check). */
  allPhasesForSaga: Phase[];
  dispatcherState: DispatcherState;
  /**
   * Whether at least one raven is available to handle this raid.
   * Resolved externally (e.g. from a raven availability check or a mock flag).
   */
  ravenResolved: boolean;
  /**
   * Whether the target execution cluster (Völundr) is reporting healthy.
   * Resolved externally from a cluster health check or a mock flag.
   */
  clusterHealthy: boolean;
}

// ---------------------------------------------------------------------------
// Engine
// ---------------------------------------------------------------------------

/**
 * Run all four feasibility gates against the supplied context.
 * Returns a result with per-gate outcomes and a top-level `feasible` flag.
 */
export function checkFeasibility(ctx: FeasibilityContext): FeasibilityResult {
  const gates: FeasibilityGate[] = [
    checkRavenResolution(ctx),
    checkConfidence(ctx),
    checkUpstreamBlocked(ctx),
    checkClusterHealth(ctx),
  ];

  return { feasible: gates.every((g) => g.passed), gates };
}

// ---------------------------------------------------------------------------
// Individual gate functions (exported for unit testing)
// ---------------------------------------------------------------------------

export function checkRavenResolution(ctx: FeasibilityContext): FeasibilityGate {
  return {
    name: 'raven_resolution',
    passed: ctx.ravenResolved,
    reason: ctx.ravenResolved
      ? 'Ravens available for assignment'
      : 'No ravens available — all slots occupied or cluster offline',
  };
}

export function checkConfidence(ctx: FeasibilityContext): FeasibilityGate {
  const passed = ctx.raid.confidence >= ctx.dispatcherState.threshold;
  return {
    name: 'confidence',
    passed,
    reason: passed
      ? `Confidence ${ctx.raid.confidence}% meets threshold ${ctx.dispatcherState.threshold}%`
      : `Confidence ${ctx.raid.confidence}% is below threshold ${ctx.dispatcherState.threshold}%`,
  };
}

export function checkUpstreamBlocked(ctx: FeasibilityContext): FeasibilityGate {
  const blocked = ctx.allPhasesForSaga.find(
    (p) => p.number < ctx.phase.number && p.status !== 'complete',
  );
  return {
    name: 'upstream_blocked',
    passed: blocked === undefined,
    reason:
      blocked === undefined
        ? 'No incomplete upstream phases'
        : `Phase "${blocked.name}" (${blocked.status}) must complete first`,
  };
}

export function checkClusterHealth(ctx: FeasibilityContext): FeasibilityGate {
  return {
    name: 'cluster_healthy',
    passed: ctx.clusterHealthy,
    reason: ctx.clusterHealthy
      ? 'Target execution cluster is healthy'
      : 'Target execution cluster is unreachable or degraded',
  };
}
