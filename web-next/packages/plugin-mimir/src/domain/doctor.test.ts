import { describe, it, expect } from 'vitest';
import { fixableChecks, type DoctorReport } from './doctor';

describe('fixableChecks', () => {
  const report: DoctorReport = {
    score: '1/3',
    worst: 'fail',
    checks: [
      {
        id: 'a',
        title: 'Passing fixable',
        status: 'pass',
        detail: '',
        remediation: '',
        fixable: true,
      },
      {
        id: 'b',
        title: 'Warn fixable',
        status: 'warn',
        detail: '',
        remediation: 'do it',
        fixable: true,
      },
      {
        id: 'c',
        title: 'Fail not fixable',
        status: 'fail',
        detail: '',
        remediation: 'manual',
        fixable: false,
      },
    ],
  };

  it('returns only non-passing fixable checks', () => {
    expect(fixableChecks(report).map((check) => check.id)).toEqual(['b']);
  });

  it('returns an empty list when nothing is fixable', () => {
    expect(fixableChecks({ score: '0/0', worst: 'pass', checks: [] })).toEqual([]);
  });
});
