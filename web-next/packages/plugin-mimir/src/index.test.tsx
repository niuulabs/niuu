import { createRootRoute } from '@tanstack/react-router';
import { describe, expect, it } from 'vitest';
import { mimirPlugin } from './index';
import { validateMemoryViewSearch } from './application/memoryViewSearch';

describe('mimirPlugin', () => {
  it('wires /mimir with the Memory Explore search-param validator', () => {
    const rootRoute = createRootRoute();
    const routes = mimirPlugin.routes?.(rootRoute) ?? [];
    const home = routes.find((r) => r.options.path === '/mimir');
    expect(home).toBeDefined();
    expect(home!.options.validateSearch).toBe(validateMemoryViewSearch);
    expect(home!.options.component).toBeDefined();
  });

  it('keeps /mimir/read, /mimir/ask, and the registry/ravns/health/lint/doctor/dreams/analytics routes live', () => {
    const rootRoute = createRootRoute();
    const routes = mimirPlugin.routes?.(rootRoute) ?? [];
    const paths = routes.map((r) => r.options.path);
    for (const kept of [
      '/mimir/read',
      '/mimir/ask',
      '/mimir/registry',
      '/mimir/ravns',
      '/mimir/health',
      '/mimir/lint',
      '/mimir/doctor',
      '/mimir/dreams',
      '/mimir/analytics',
    ]) {
      expect(paths, `missing ${kept}`).toContain(kept);
    }
  });
});
