import { describe, it, expect } from 'vitest';
import {
  sagaStatusSchema,
  phaseStatusSchema,
  raidStatusSchema,
  sagaSchema,
  phaseSchema,
  raidSchema,
  confidenceEventSchema,
} from './saga';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const validSaga = {
  id: '00000000-0000-0000-0000-000000000001',
  trackerId: 'LIN-001',
  trackerType: 'linear',
  slug: 'auth-rewrite',
  name: 'Auth Rewrite',
  repos: ['niuulabs/volundr'],
  featureBranch: 'feat/auth-rewrite',
  status: 'active' as const,
  confidence: 72,
  createdAt: '2026-01-01T00:00:00Z',
  phaseSummary: { total: 3, completed: 1 },
};

const validRaid = {
  id: '00000000-0000-0000-0000-000000000002',
  phaseId: '00000000-0000-0000-0000-000000000010',
  trackerId: 'LIN-002',
  name: 'Implement JWT refresh',
  description: 'Add silent token refresh to the auth flow.',
  acceptanceCriteria: ['Token refreshes before expiry', 'No logout on tab focus'],
  declaredFiles: ['src/auth/refresh.ts'],
  estimateHours: 4,
  status: 'queued' as const,
  confidence: 80,
  sessionId: null,
  reviewerSessionId: null,
  reviewRound: 0,
  branch: null,
  chronicleSummary: null,
  retryCount: 0,
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
};

const validPhase = {
  id: '00000000-0000-0000-0000-000000000010',
  sagaId: '00000000-0000-0000-0000-000000000001',
  trackerId: 'LIN-M1',
  number: 1,
  name: 'Phase 1: Foundation',
  status: 'active' as const,
  confidence: 75,
  raids: [validRaid],
};

// ---------------------------------------------------------------------------
// Status schemas
// ---------------------------------------------------------------------------

describe('sagaStatusSchema', () => {
  it('accepts valid statuses', () => {
    expect(sagaStatusSchema.parse('active')).toBe('active');
    expect(sagaStatusSchema.parse('complete')).toBe('complete');
    expect(sagaStatusSchema.parse('failed')).toBe('failed');
  });

  it('rejects unknown values', () => {
    expect(() => sagaStatusSchema.parse('unknown')).toThrow();
    expect(() => sagaStatusSchema.parse('')).toThrow();
  });
});

describe('phaseStatusSchema', () => {
  it('accepts all phase statuses', () => {
    for (const s of ['pending', 'active', 'gated', 'complete']) {
      expect(phaseStatusSchema.parse(s)).toBe(s);
    }
  });

  it('rejects invalid status', () => {
    expect(() => phaseStatusSchema.parse('cancelled')).toThrow();
  });
});

describe('raidStatusSchema', () => {
  it('accepts all raid statuses', () => {
    for (const s of ['pending', 'queued', 'running', 'review', 'escalated', 'merged', 'failed']) {
      expect(raidStatusSchema.parse(s)).toBe(s);
    }
  });

  it('rejects invalid status', () => {
    expect(() => raidStatusSchema.parse('done')).toThrow();
  });
});

// ---------------------------------------------------------------------------
// Saga schema
// ---------------------------------------------------------------------------

describe('sagaSchema', () => {
  it('parses a valid saga', () => {
    const result = sagaSchema.parse(validSaga);
    expect(result.id).toBe(validSaga.id);
    expect(result.status).toBe('active');
    expect(result.phaseSummary.total).toBe(3);
  });

  it('rejects confidence outside 0–100', () => {
    expect(() => sagaSchema.parse({ ...validSaga, confidence: 101 })).toThrow();
    expect(() => sagaSchema.parse({ ...validSaga, confidence: -1 })).toThrow();
  });

  it('rejects invalid UUID', () => {
    expect(() => sagaSchema.parse({ ...validSaga, id: 'not-a-uuid' })).toThrow();
  });

  it('rejects empty name', () => {
    expect(() => sagaSchema.parse({ ...validSaga, name: '' })).toThrow();
  });

  it('accepts confidence at boundary values', () => {
    expect(sagaSchema.parse({ ...validSaga, confidence: 0 }).confidence).toBe(0);
    expect(sagaSchema.parse({ ...validSaga, confidence: 100 }).confidence).toBe(100);
  });
});

// ---------------------------------------------------------------------------
// Raid schema
// ---------------------------------------------------------------------------

describe('raidSchema', () => {
  it('parses a valid raid', () => {
    const result = raidSchema.parse(validRaid);
    expect(result.name).toBe('Implement JWT refresh');
    expect(result.acceptanceCriteria).toHaveLength(2);
  });

  it('accepts null estimateHours', () => {
    const result = raidSchema.parse({ ...validRaid, estimateHours: null });
    expect(result.estimateHours).toBeNull();
  });

  it('accepts null sessionId and branch', () => {
    const result = raidSchema.parse(validRaid);
    expect(result.sessionId).toBeNull();
    expect(result.branch).toBeNull();
  });

  it('rejects negative retryCount', () => {
    expect(() => raidSchema.parse({ ...validRaid, retryCount: -1 })).toThrow();
  });
});

// ---------------------------------------------------------------------------
// Phase schema
// ---------------------------------------------------------------------------

describe('phaseSchema', () => {
  it('parses a valid phase with raids', () => {
    const result = phaseSchema.parse(validPhase);
    expect(result.raids).toHaveLength(1);
    expect(result.raids[0]?.name).toBe('Implement JWT refresh');
  });

  it('accepts empty raids array', () => {
    const result = phaseSchema.parse({ ...validPhase, raids: [] });
    expect(result.raids).toHaveLength(0);
  });

  it('rejects zero phase number', () => {
    expect(() => phaseSchema.parse({ ...validPhase, number: 0 })).toThrow();
  });
});

// ---------------------------------------------------------------------------
// ConfidenceEvent schema
// ---------------------------------------------------------------------------

describe('confidenceEventSchema', () => {
  const validEvent = {
    id: '00000000-0000-0000-0000-000000000099',
    raidId: '00000000-0000-0000-0000-000000000002',
    eventType: 'ci_pass' as const,
    delta: 5,
    scoreAfter: 85,
    createdAt: '2026-01-01T00:00:00Z',
  };

  it('parses a valid confidence event', () => {
    const result = confidenceEventSchema.parse(validEvent);
    expect(result.eventType).toBe('ci_pass');
    expect(result.delta).toBe(5);
  });

  it('accepts negative delta for penalty events', () => {
    const result = confidenceEventSchema.parse({ ...validEvent, delta: -10, scoreAfter: 70 });
    expect(result.delta).toBe(-10);
  });

  it('rejects scoreAfter outside 0–100', () => {
    expect(() => confidenceEventSchema.parse({ ...validEvent, scoreAfter: 101 })).toThrow();
  });
});
