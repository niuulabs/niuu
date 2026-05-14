import { describe, expect, it } from 'vitest';
import { phaseStatusToCell, phaseStatusToStateDot } from './mappers';

describe('phase status mappers', () => {
  it.each([
    ['complete', 'ok'],
    ['active', 'run'],
    ['gated', 'gate'],
    ['pending', 'pend'],
  ] as const)('maps phase status %s to pipe cell %s', (status, expected) => {
    expect(phaseStatusToCell(status)).toBe(expected);
  });

  it.each([
    ['complete', 'merged'],
    ['active', 'running'],
    ['gated', 'attention'],
    ['pending', 'idle'],
  ] as const)('maps phase status %s to state dot %s', (status, expected) => {
    expect(phaseStatusToStateDot(status)).toBe(expected);
  });
});
