import { describe, it, expect } from 'vitest';
import { describeLiveActivity } from './liveActivityText';

describe('describeLiveActivity', () => {
  it('describes a read event with a named actor', () => {
    expect(describeLiveActivity({ actor: 'muninn', kind: 'read' }, 'Gateway routing')).toBe(
      'muninn is reading Gateway routing',
    );
  });
  it('describes a write event with a null actor as "Someone"', () => {
    expect(describeLiveActivity({ actor: null, kind: 'write' }, 'OpenBao policy')).toBe(
      'Someone wrote to OpenBao policy',
    );
  });
});
