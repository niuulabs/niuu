import { describe, expect, it } from 'vitest';
import { toLifecycleState } from './toLifecycleState';

describe('toLifecycleState', () => {
  it('maps requested sessions to provisioning', () => {
    expect(toLifecycleState('requested')).toBe('provisioning');
  });

  it('passes through non-requested states', () => {
    expect(toLifecycleState('running')).toBe('running');
  });
});
