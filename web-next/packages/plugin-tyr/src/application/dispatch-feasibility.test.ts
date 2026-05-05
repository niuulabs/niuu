import { describe, it, expect } from 'vitest';
import {
  checkFeasibility,
  checkRavenResolution,
  checkConfidence,
  checkUpstreamBlocked,
  checkClusterHealth,
  type FeasibilityContext,
} from './dispatch-feasibility';
import type { Raid, Phase } from '../domain/saga';
import type { DispatcherState } from '../domain/dispatcher';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const DISPATCHER: DispatcherState = {
  id: '00000000-0000-0000-0000-000000000000',
  running: true,
  threshold: 70,
  maxConcurrentRaids: 3,
  autoContinue: false,
  updatedAt: '2026-01-01T00:00:00Z',
};

function makeRaid(overrides: Partial<Raid> = {}): Raid {
  return {
    id: '00000000-0000-0000-0000-000000000001',
    phaseId: '00000000-0000-0000-0000-000000000100',
    trackerId: 'NIU-001',
    name: 'Test raid',
    description: 'A test raid',
    acceptanceCriteria: [],
    declaredFiles: [],
    estimateHours: 4,
    status: 'pending',
    confidence: 80,
    sessionId: null,
    reviewerSessionId: null,
    reviewRound: 0,
    branch: null,
    chronicleSummary: null,
    retryCount: 0,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function makePhase(overrides: Partial<Phase> = {}): Phase {
  return {
    id: '00000000-0000-0000-0000-000000000100',
    sagaId: '00000000-0000-0000-0000-000000000001',
    trackerId: 'NIU-M1',
    number: 2,
    name: 'Phase 2',
    status: 'active',
    confidence: 80,
    raids: [],
    ...overrides,
  };
}

function makePhaseWithNumber(number: number, status: Phase['status'] = 'complete'): Phase {
  return makePhase({
    id: `00000000-0000-0000-0000-00000000010${number}`,
    number,
    name: `Phase ${number}`,
    status,
  });
}

function makeCtx(overrides: Partial<FeasibilityContext> = {}): FeasibilityContext {
  const phase = makePhase();
  return {
    raid: makeRaid(),
    phase,
    allPhasesForSaga: [phase],
    dispatcherState: DISPATCHER,
    ravenResolved: true,
    clusterHealthy: true,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Gate 1: raven_resolution
// ---------------------------------------------------------------------------

describe('checkRavenResolution', () => {
  it('passes when ravens are available', () => {
    const result = checkRavenResolution(makeCtx({ ravenResolved: true }));
    expect(result.passed).toBe(true);
    expect(result.name).toBe('raven_resolution');
    expect(result.reason).toMatch(/available/i);
  });

  it('fails when no ravens are available', () => {
    const result = checkRavenResolution(makeCtx({ ravenResolved: false }));
    expect(result.passed).toBe(false);
    expect(result.reason).toMatch(/no ravens/i);
  });
});

// ---------------------------------------------------------------------------
// Gate 2: confidence
// ---------------------------------------------------------------------------

describe('checkConfidence', () => {
  it('passes when confidence equals threshold', () => {
    const ctx = makeCtx({ raid: makeRaid({ confidence: 70 }) });
    const result = checkConfidence(ctx);
    expect(result.passed).toBe(true);
    expect(result.reason).toContain('70%');
  });

  it('passes when confidence exceeds threshold', () => {
    const ctx = makeCtx({ raid: makeRaid({ confidence: 90 }) });
    const result = checkConfidence(ctx);
    expect(result.passed).toBe(true);
  });

  it('fails when confidence is below threshold', () => {
    const ctx = makeCtx({ raid: makeRaid({ confidence: 50 }) });
    const result = checkConfidence(ctx);
    expect(result.passed).toBe(false);
    expect(result.name).toBe('confidence');
    expect(result.reason).toContain('50%');
    expect(result.reason).toContain('70%');
  });

  it('fails at confidence zero', () => {
    const ctx = makeCtx({ raid: makeRaid({ confidence: 0 }) });
    const result = checkConfidence(ctx);
    expect(result.passed).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Gate 3: upstream_blocked
// ---------------------------------------------------------------------------

describe('checkUpstreamBlocked', () => {
  it('passes with no upstream phases', () => {
    const phase = makePhase({ number: 1 });
    const ctx = makeCtx({ phase, allPhasesForSaga: [phase] });
    const result = checkUpstreamBlocked(ctx);
    expect(result.passed).toBe(true);
    expect(result.name).toBe('upstream_blocked');
  });

  it('passes when all upstream phases are complete', () => {
    const phase1 = makePhaseWithNumber(1, 'complete');
    const phase2 = makePhase({ number: 2, status: 'active' });
    const ctx = makeCtx({ phase: phase2, allPhasesForSaga: [phase1, phase2] });
    const result = checkUpstreamBlocked(ctx);
    expect(result.passed).toBe(true);
  });

  it('fails when an upstream phase is pending', () => {
    const phase1 = makePhaseWithNumber(1, 'pending');
    const phase2 = makePhase({ number: 2 });
    const ctx = makeCtx({ phase: phase2, allPhasesForSaga: [phase1, phase2] });
    const result = checkUpstreamBlocked(ctx);
    expect(result.passed).toBe(false);
    expect(result.reason).toContain('Phase 1');
    expect(result.reason).toContain('pending');
  });

  it('fails when an upstream phase is gated', () => {
    const phase1 = makePhaseWithNumber(1, 'gated');
    const phase2 = makePhase({ number: 2 });
    const ctx = makeCtx({ phase: phase2, allPhasesForSaga: [phase1, phase2] });
    const result = checkUpstreamBlocked(ctx);
    expect(result.passed).toBe(false);
    expect(result.reason).toContain('gated');
  });

  it('does not consider downstream phases', () => {
    const phase1 = makePhaseWithNumber(1, 'complete');
    const phase2 = makePhase({ number: 2, status: 'active' });
    const phase3 = makePhaseWithNumber(3, 'pending');
    const ctx = makeCtx({ phase: phase2, allPhasesForSaga: [phase1, phase2, phase3] });
    const result = checkUpstreamBlocked(ctx);
    expect(result.passed).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Gate 4: cluster_healthy
// ---------------------------------------------------------------------------

describe('checkClusterHealth', () => {
  it('passes when cluster is healthy', () => {
    const result = checkClusterHealth(makeCtx({ clusterHealthy: true }));
    expect(result.passed).toBe(true);
    expect(result.name).toBe('cluster_healthy');
    expect(result.reason).toMatch(/healthy/i);
  });

  it('fails when cluster is unhealthy', () => {
    const result = checkClusterHealth(makeCtx({ clusterHealthy: false }));
    expect(result.passed).toBe(false);
    expect(result.reason).toMatch(/unreachable|degraded/i);
  });
});

// ---------------------------------------------------------------------------
// checkFeasibility — combined
// ---------------------------------------------------------------------------

describe('checkFeasibility', () => {
  it('is feasible when all gates pass', () => {
    const result = checkFeasibility(makeCtx());
    expect(result.feasible).toBe(true);
    expect(result.gates).toHaveLength(4);
    expect(result.gates.every((g) => g.passed)).toBe(true);
  });

  it('is not feasible when raven gate fails', () => {
    const result = checkFeasibility(makeCtx({ ravenResolved: false }));
    expect(result.feasible).toBe(false);
    const gate = result.gates.find((g) => g.name === 'raven_resolution');
    expect(gate?.passed).toBe(false);
  });

  it('is not feasible when confidence gate fails', () => {
    const result = checkFeasibility(makeCtx({ raid: makeRaid({ confidence: 10 }) }));
    expect(result.feasible).toBe(false);
    const gate = result.gates.find((g) => g.name === 'confidence');
    expect(gate?.passed).toBe(false);
  });

  it('is not feasible when upstream is blocked', () => {
    const phase1 = makePhaseWithNumber(1, 'pending');
    const phase2 = makePhase({ number: 2 });
    const result = checkFeasibility(makeCtx({ phase: phase2, allPhasesForSaga: [phase1, phase2] }));
    expect(result.feasible).toBe(false);
    const gate = result.gates.find((g) => g.name === 'upstream_blocked');
    expect(gate?.passed).toBe(false);
  });

  it('is not feasible when cluster is unhealthy', () => {
    const result = checkFeasibility(makeCtx({ clusterHealthy: false }));
    expect(result.feasible).toBe(false);
    const gate = result.gates.find((g) => g.name === 'cluster_healthy');
    expect(gate?.passed).toBe(false);
  });

  it('returns all gates even when multiple fail', () => {
    const result = checkFeasibility(
      makeCtx({
        ravenResolved: false,
        clusterHealthy: false,
        raid: makeRaid({ confidence: 10 }),
      }),
    );
    expect(result.feasible).toBe(false);
    const failing = result.gates.filter((g) => !g.passed);
    expect(failing.length).toBeGreaterThanOrEqual(3);
  });

  it('always returns exactly 4 gates', () => {
    const result = checkFeasibility(makeCtx());
    expect(result.gates).toHaveLength(4);
    const names = result.gates.map((g) => g.name);
    expect(names).toContain('raven_resolution');
    expect(names).toContain('confidence');
    expect(names).toContain('upstream_blocked');
    expect(names).toContain('cluster_healthy');
  });
});
