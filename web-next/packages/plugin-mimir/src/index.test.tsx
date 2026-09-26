import { createRootRoute, isRedirect } from '@tanstack/react-router';
import { describe, expect, it } from 'vitest';
import { mimirPlugin } from './index';
import { MemoryExploreView } from './ui/memory/MemoryExploreView';
import { validateMemoryViewSearch } from './application/memoryViewSearch';

function routesOf() {
  const rootRoute = createRootRoute();
  return mimirPlugin.routes?.(rootRoute) ?? [];
}

function routeAt(path: string) {
  const route = routesOf().find((r) => r.options.path === path);
  if (!route) throw new Error(`no route for ${path}`);
  return route;
}

/** Runs a route's `beforeLoad` and returns the `redirect()` it throws. */
function caughtRedirect(path: string, search: Record<string, unknown> = {}) {
  const route = routeAt(path);
  const beforeLoad = route.options.beforeLoad as (ctx: unknown) => unknown;
  try {
    beforeLoad({ location: { search } });
  } catch (thrown) {
    if (!isRedirect(thrown)) throw thrown;
    return thrown;
  }
  throw new Error(`beforeLoad for ${path} did not redirect`);
}

describe('mimirPlugin', () => {
  it('wires /mimir with the Memory Explore search-param validator and renders the scene in both modes', () => {
    const home = routeAt('/mimir');
    expect(home.options.validateSearch).toBe(validateMemoryViewSearch);
    expect(home.options.component).toBe(MemoryExploreView);
  });

  it('Memory is the only Simple-mode tab, and tabs are Memory + Registry', () => {
    expect(mimirPlugin.simple?.tabs).toEqual(['memory']);
    expect(mimirPlugin.tabs?.map((t) => t.id)).toEqual(['memory', 'registry']);
    expect(mimirPlugin.tabs?.map((t) => t.path)).toEqual(['/mimir', '/mimir/registry']);
  });

  it('keeps /mimir/read and the registry/ravns/health/lint/doctor/dreams/analytics routes live with their real components', () => {
    const paths = routesOf().map((r) => r.options.path);
    for (const kept of [
      '/mimir/read',
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
    expect(routeAt('/mimir/registry').options.component).toBeDefined();
  });

  it('/mimir/ask redirects to /mimir carrying q and mount, answered inline in the scene now', () => {
    const redirected = caughtRedirect('/mimir/ask', { q: 'why does it break?', mount: 'platform' });
    expect(redirected.options.to).toBe('/mimir');
    expect(redirected.options.search).toEqual({ q: 'why does it break?', mount: 'platform' });
  });

  it('/mimir/ask redirects without a mount when none was given', () => {
    const redirected = caughtRedirect('/mimir/ask', { q: 'hello' });
    expect(redirected.options.search).toEqual({ q: 'hello' });
  });

  for (const legacy of ['pages', 'sources', 'search', 'graph', 'ingest', 'entities']) {
    it(`legacy /mimir/${legacy} redirects to /mimir, keeping only the mount`, () => {
      const redirected = caughtRedirect(`/mimir/${legacy}`, {
        mount: 'platform',
        category: 'infra',
      });
      expect(redirected.options.to).toBe('/mimir');
      expect(redirected.options.search).toEqual({ mount: 'platform' });
    });
  }
});
