import { describe, it, expect } from 'vitest';
import { createQueryClient } from './index';

describe('createQueryClient', () => {
  it('returns a QueryClient with Niuu defaults', () => {
    const client = createQueryClient();
    const defaults = client.getDefaultOptions();
    expect(defaults.queries?.staleTime).toBe(30_000);
    expect(defaults.queries?.retry).toBe(1);
    expect(defaults.queries?.refetchOnWindowFocus).toBe(false);
    expect(defaults.mutations?.retry).toBe(0);
  });

  it('merges caller overrides on top of defaults', () => {
    const client = createQueryClient({
      defaultOptions: { queries: { retry: 5 } },
    });
    expect(client.getDefaultOptions().queries?.retry).toBe(5);
    expect(client.getDefaultOptions().queries?.staleTime).toBe(30_000);
  });
});
