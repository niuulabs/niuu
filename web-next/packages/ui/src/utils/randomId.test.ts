import { describe, it, expect, afterEach } from 'vitest';
import { randomId } from './randomId';

const UUID_V4_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe('randomId', () => {
  const original = crypto.randomUUID;

  afterEach(() => {
    Object.defineProperty(crypto, 'randomUUID', {
      value: original,
      configurable: true,
      writable: true,
    });
  });

  it('returns a UUID v4 when crypto.randomUUID is available', () => {
    const id = randomId();
    expect(id).toMatch(UUID_V4_RE);
  });

  it('falls back to getRandomValues when randomUUID is missing (non-secure context)', () => {
    Object.defineProperty(crypto, 'randomUUID', {
      value: undefined,
      configurable: true,
      writable: true,
    });
    const id = randomId();
    expect(id).toMatch(UUID_V4_RE);
  });

  it('produces distinct ids on consecutive calls', () => {
    expect(randomId()).not.toBe(randomId());
  });
});
