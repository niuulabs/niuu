import { describe, it, expect } from 'vitest';
import { isAutoFixable, tallySeverity } from './lint';
import type { LintIssue } from './lint';

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
    const issue = makeIssue({ rule: 'L12', severity: 'info' });
    expect(issue.rule).toBe('L12');
    expect(issue.severity).toBe('info');
    expect(issue.page).toBe('/arch/overview');
    expect(issue.mount).toBe('local');
  });
});
