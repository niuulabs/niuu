import { createRootRoute } from '@tanstack/react-router';
import { UI_MODE_STORAGE_KEY } from '@niuulabs/shell';
import { afterEach, describe, expect, it } from 'vitest';
import { volundrPlugin } from './index';

/** The path a thrown TanStack redirect points at. */
function redirectTarget(beforeLoad: unknown): string {
  try {
    (beforeLoad as (ctx: unknown) => void)({ location: { search: {} } });
  } catch (thrown) {
    return (thrown as { options: { to: string } }).options.to;
  }
  throw new Error('route did not redirect');
}

describe('volundrPlugin', () => {
  afterEach(() => {
    localStorage.clear();
  });

  it('is Sessions in Simple mode, with only the sessions tab', () => {
    expect(volundrPlugin.simple).toMatchObject({
      tabs: ['sessions'],
      title: 'Sessions',
      subtitle: 'coding agents in sandboxes',
    });
  });

  it('keeps Forge at the beginning of the tab list while sessions use their dedicated route', () => {
    expect(volundrPlugin.tabs).toEqual([
      { id: 'forge', label: 'Forge', path: '/volundr/forge' },
      { id: 'sessions', label: 'Sessions', path: '/volundr/sessions' },
      { id: 'catalog', label: 'Catalog', path: '/volundr/catalog' },
    ]);
  });

  it('routes the plugin root to Forge while keeping the sessions shell and legacy redirects available', () => {
    const rootRoute = createRootRoute();
    const routes = volundrPlugin.routes?.(rootRoute) ?? [];
    const paths = routes.map((route) => route.options.path);

    expect(paths).toContain('/volundr');
    expect(paths).toContain('/volundr/forge');
    expect(paths).toContain('/volundr/sessions');
    expect(paths).toContain('/volundr/sessions/new');
    expect(paths).toContain('/volundr/sessions/$sessionId');
    expect(paths).toContain('/volundr/catalog');
    expect(paths).not.toContain('/volundr/templates');
    expect(paths).toContain('/volundr/credentials');
    expect(paths).toContain('/volundr/clusters');
  });
});

describe('volundr root redirect', () => {
  afterEach(() => {
    localStorage.clear();
  });

  function rootBeforeLoad() {
    const routes = volundrPlugin.routes?.(createRootRoute()) ?? [];
    const root = routes.find((route) => route.options.path === '/volundr');
    return (root?.options as { beforeLoad: unknown }).beforeLoad;
  }

  it('sends Simple mode to the session list', () => {
    localStorage.setItem(UI_MODE_STORAGE_KEY, 'simple');
    expect(redirectTarget(rootBeforeLoad())).toBe('/volundr/sessions');
  });

  it('keeps Advanced mode on the forge', () => {
    localStorage.setItem(UI_MODE_STORAGE_KEY, 'advanced');
    expect(redirectTarget(rootBeforeLoad())).toBe('/volundr/forge');
  });
});
