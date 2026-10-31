import { describe, expect, it } from 'vitest';
import { ApiClientError } from '@niuulabs/query';
import { describeError } from './errors';

describe('describeError', () => {
  it('prefers the API detail, then the message, then the fallback', () => {
    expect(
      describeError(new ApiClientError('API request failed: 500', 500, 'store down'), 'x'),
    ).toBe('store down');
    expect(describeError(new ApiClientError('API request failed: 500', 500, ' '), 'x')).toBe(
      'API request failed: 500',
    );
    expect(
      describeError(
        new ApiClientError('API request failed: 409', 409, {
          message: 'read state changed on thor',
          conflicts: ['thor'],
        } as never),
        'x',
      ),
    ).toBe('read state changed on thor');
    expect(
      describeError(new ApiClientError('API request failed: 422', 422, [] as never), 'x'),
    ).toBe('API request failed: 422');
    expect(describeError(new Error(''), 'fallback')).toBe('fallback');
    expect(describeError('offline', 'fallback')).toBe('fallback');
    expect(describeError(null, 'fallback')).toBe('fallback');
  });
});
