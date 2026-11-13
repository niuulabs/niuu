import { createRootRoute, isRedirect } from '@tanstack/react-router';
import { describe, expect, it } from 'vitest';
import { mimirPlugin } from './index';
import { MemoryExploreView } from './ui/memory/MemoryExploreView';
import { validateMemoryViewSearch } from './application/memoryViewSearch';
import { AskPage } from './ui/everyday/AskPage';
import { ReviewPage } from './ui/everyday/ReviewPage';

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

  it('has Explore, Ask, Review and Library tabs', () => {
    expect(mimirPlugin.tabs?.map((t) => t.id)).toEqual(['memory', 'ask', 'review', 'registry']);
    expect(mimirPlugin.tabs?.map((t) => t.path)).toEqual([
      '/mimir',
      '/mimir/ask',
      '/mimir/review',
      '/mimir/registry',
    ]);
  });

  it('keeps /mimir/read and the registry/analytics routes live with their real components', () => {
    const paths = routesOf().map((r) => r.options.path);
    for (const kept of [
      '/mimir/read',
      '/mimir/registry',
      '/mimir/registry/analytics',
      '/mimir/analytics',
    ]) {
      expect(paths, `missing ${kept}`).toContain(kept);
    }
    expect(routeAt('/mimir/registry').options.component).toBeDefined();
  });

  it('no longer serves the retired health, lint or doctor views', () => {
    const paths = routesOf().map((r) => r.options.path);
    expect(paths).not.toContain('/mimir/registry/health');
    for (const retired of ['health', 'lint', 'doctor']) {
      expect(routeAt(`/mimir/${retired}`).options.component).toBeUndefined();
    }
  });

  it('serves Ask and Review as their own pages', () => {
    expect(routeAt('/mimir/ask').options.component).toBe(AskPage);
    expect(routeAt('/mimir/review').options.component).toBe(ReviewPage);
  });

  for (const legacy of [
    'pages',
    'sources',
    'search',
    'graph',
    'ingest',
    'entities',
    'health',
    'lint',
    'doctor',
  ]) {
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
