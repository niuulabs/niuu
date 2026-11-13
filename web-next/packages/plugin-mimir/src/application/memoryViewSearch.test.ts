import { describe, it, expect } from 'vitest';
import { validateMemoryViewSearch } from './memoryViewSearch';

describe('validateMemoryViewSearch', () => {
  it('passes through all valid fields', () => {
    expect(
      validateMemoryViewSearch({
        mount: 'platform',
        focus: '/a',
        depth: 2,
        q: 'why do routes 403?',
        asOf: '2026-04-01',
        view: '2d',
        show: 'people',
      }),
    ).toEqual({
      mount: 'platform',
      focus: '/a',
      depth: 2,
      q: 'why do routes 403?',
      asOf: '2026-04-01',
      view: '2d',
      show: 'people',
    });
  });

  it('drops invalid depth/view/show rather than throwing', () => {
    expect(validateMemoryViewSearch({ depth: 9, view: 'vr', show: 'robots' })).toEqual({
      mount: undefined,
      focus: undefined,
      depth: undefined,
      q: undefined,
      asOf: undefined,
      view: undefined,
      show: undefined,
    });
  });

  it('treats an empty string as absent', () => {
    expect(validateMemoryViewSearch({ mount: '', q: '' }).mount).toBeUndefined();
    expect(validateMemoryViewSearch({ mount: '', q: '' }).q).toBeUndefined();
  });

  it('handles a bare empty search object', () => {
    expect(validateMemoryViewSearch({})).toEqual({
      mount: undefined,
      focus: undefined,
      depth: undefined,
      q: undefined,
      asOf: undefined,
      view: undefined,
      show: undefined,
    });
  });
});
