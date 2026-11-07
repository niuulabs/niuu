import { describe, expect, it } from 'vitest';
import { errorText } from './errorText';

describe('errorText', () => {
  it('prefers the server detail, unwrapping one nested layer', () => {
    expect(errorText(Object.assign(new Error('x'), { detail: 'registry offline' }), 'f')).toBe(
      'registry offline',
    );
    expect(
      errorText(Object.assign(new Error('x'), { detail: '{"detail":"inner reason"}' }), 'f'),
    ).toBe('inner reason');
    expect(errorText(Object.assign(new Error('x'), { detail: '{not json' }), 'f')).toBe(
      '{not json',
    );
    expect(errorText(Object.assign(new Error('x'), { detail: '{"other":1}' }), 'f')).toBe(
      '{"other":1}',
    );
  });

  it('falls back to the message, then to the caller text', () => {
    expect(errorText(new Error('API request failed: 503'), 'f')).toBe('API request failed: 503');
    expect(errorText(new Error(''), 'fallback')).toBe('fallback');
    expect(errorText(undefined, 'fallback')).toBe('fallback');
  });
});
