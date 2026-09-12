import fc from 'fast-check';
import { afterEach, describe, expect, it } from 'vitest';
import { getWebSocketAuth, setTokenProvider, withAuthQuery } from './http-client';

// Runs in the coverage-gated suite on every PR. fast-check reports a seed and
// shrink path on failure so a counterexample can be replayed locally.
describe('credential URL fuzzing', () => {
  afterEach(() => setTokenProvider(null));

  it('keeps arbitrary credentials inside one query value without changing the destination', () => {
    fc.assert(
      fc.property(fc.string({ minLength: 1 }), fc.string(), (token, existingValue) => {
        setTokenProvider(() => token);
        const input = new URL('https://api.example.test/events');
        input.searchParams.set('cursor', existingValue);
        input.hash = 'anchor';

        for (const [result, credentialKey] of [
          [withAuthQuery(input.href), 'access_token'],
          [getWebSocketAuth(input.href).url, 'token'],
        ] as const) {
          const parsed = new URL(result);
          expect(parsed.origin).toBe(input.origin);
          expect(parsed.pathname).toBe(input.pathname);
          expect(parsed.hash).toBe(input.hash);
          expect([...parsed.searchParams.keys()]).toEqual(['cursor', credentialKey]);
          expect(parsed.searchParams.get('cursor')).toBe(existingValue);
          expect(parsed.searchParams.get(credentialKey)).toBe(token);
        }
      }),
      {
        numRuns: 1000,
        examples: [
          ['x&admin=true#fragment', 'a=b&c=d'],
          ['a?b/c+%20=雪', '#&?'],
        ],
      },
    );
  });
});
