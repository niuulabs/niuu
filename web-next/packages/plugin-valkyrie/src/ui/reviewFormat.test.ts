import { describe, expect, it } from 'vitest';
import type { ReviewItem } from '../domain';
import { riskClasses, sortByUrgency, statusClasses, timeAgo } from './reviewFormat';

function item(overrides: Partial<ReviewItem>): ReviewItem {
  return {
    itemId: 'review:x',
    kind: 'evolution_build',
    requestedAction: 'install',
    environmentId: 'env',
    valkyrieId: 'v',
    title: 't',
    summary: '',
    audience: 'valkyrie',
    flockId: '',
    domain: '',
    riskClass: 'low',
    safetyClass: 'read_only',
    urgency: 0.5,
    requestedCapability: 'approve',
    evidence: {},
    status: 'pending',
    requestedBy: '',
    requestedAt: '2026-06-03T13:00:00Z',
    decidedBy: '',
    decidedAt: '',
    decisionReason: '',
    resolvedAt: '',
    applyOutcome: '',
    applyDetail: '',
    ...overrides,
  };
}

describe('reviewFormat', () => {
  it('maps risk and status to token classes', () => {
    expect(riskClasses('critical')).toContain('critical');
    expect(riskClasses('medium')).toContain('warn');
    expect(riskClasses('low')).toContain('muted');
    expect(statusClasses('pending')).toContain('warn');
    expect(statusClasses('apply_failed')).toContain('critical');
    expect(statusClasses('applied')).toContain('ok');
    expect(statusClasses('expired')).toContain('muted');
  });

  it('renders compact relative time', () => {
    const now = new Date('2026-06-03T14:00:00Z');
    expect(timeAgo('2026-06-03T13:59:30Z', now)).toBe('30s');
    expect(timeAgo('2026-06-03T13:10:00Z', now)).toBe('50m');
    expect(timeAgo('2026-06-03T02:00:00Z', now)).toBe('12h');
    expect(timeAgo('2026-05-30T14:00:00Z', now)).toBe('4d');
    expect(timeAgo('')).toBe('—');
    expect(timeAgo('not-a-date')).toBe('not-a-date');
  });

  it('sorts by urgency then recency', () => {
    const sorted = sortByUrgency([
      item({ itemId: 'a', urgency: 0.4, requestedAt: '2026-06-03T13:00:00Z' }),
      item({ itemId: 'b', urgency: 0.9, requestedAt: '2026-06-03T12:00:00Z' }),
      item({ itemId: 'c', urgency: 0.4, requestedAt: '2026-06-03T13:30:00Z' }),
    ]);
    expect(sorted.map((entry) => entry.itemId)).toEqual(['b', 'c', 'a']);
  });
});
