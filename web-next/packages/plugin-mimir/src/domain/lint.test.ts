import { describe, it, expect } from 'vitest';
import { isAutoFixable, tallySeverity } from './lint';
import type { LintIssue, DreamCycle } from './lint';

const makeIssue = (overrides: Partial<LintIssue> = {}): LintIssue => ({
  id: 'lint-001',
  rule: 'L05',
  severity: 'error',
  page: '/arch/overview',
  mount: 'local',
  autoFix: false,
  message: 'Broken wikilink',
  ...overrides,
});

describe('isAutoFixable', () => {
  it('returns true when autoFix is true', () => {
    expect(isAutoFixable(makeIssue({ autoFix: true }))).toBe(true);
  });

  it('returns false when autoFix is false', () => {
    expect(isAutoFixable(makeIssue({ autoFix: false }))).toBe(false);
  });
});

describe('tallySeverity', () => {
  it('counts each severity level', () => {
    const issues: LintIssue[] = [
      makeIssue({ severity: 'error' }),
      makeIssue({ severity: 'error' }),
      makeIssue({ severity: 'warn' }),
      makeIssue({ severity: 'info' }),
    ];
    expect(tallySeverity(issues)).toEqual({ error: 2, warn: 1, info: 1 });
  });

  it('returns all zeroes for empty list', () => {
    expect(tallySeverity([])).toEqual({ error: 0, warn: 0, info: 0 });
  });

  it('handles all-info list', () => {
    const issues = [makeIssue({ severity: 'info' }), makeIssue({ severity: 'info' })];
    expect(tallySeverity(issues)).toEqual({ error: 0, warn: 0, info: 2 });
  });
});

describe('LintIssue structure', () => {
  it('carries rule code, severity, page, mount, and message', () => {
    const issue = makeIssue({ rule: 'L12', severity: 'info', assignee: 'ravn-skald' });
    expect(issue.rule).toBe('L12');
    expect(issue.severity).toBe('info');
    expect(issue.page).toBe('/arch/overview');
    expect(issue.mount).toBe('local');
    expect(issue.assignee).toBe('ravn-skald');
  });
});

describe('DreamCycle structure', () => {
  it('carries all required fields', () => {
    const cycle: DreamCycle = {
      id: 'dream-001',
      timestamp: '2026-04-19T03:00:00Z',
      ravn: 'ravn-fjolnir',
      mounts: ['local', 'shared'],
      pagesUpdated: 8,
      entitiesCreated: 2,
      lintFixes: 1,
      durationMs: 42000,
    };
    expect(cycle.id).toBe('dream-001');
    expect(cycle.mounts).toHaveLength(2);
    expect(cycle.durationMs).toBe(42000);
  });
});
