import { createRootRoute } from '@tanstack/react-router';
import { afterEach, describe, expect, it } from 'vitest';
import { volundrPlugin } from './index';
import { useUnreadNotificationCount } from './ui/hooks/useNotifications';

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

  it('is called Forge', () => {
    expect(volundrPlugin).toMatchObject({
      title: 'Forge',
      subtitle: 'build and execute',
    });
  });

  it('keeps Overview at the beginning of the tab list while sessions use their dedicated route', () => {
    expect(volundrPlugin.tabs).toEqual([
      { id: 'forge', label: 'Overview', path: '/volundr/forge' },
      { id: 'sessions', label: 'Sessions', path: '/volundr/sessions' },
      { id: 'catalog', label: 'Catalog', path: '/volundr/catalog' },
      {
        id: 'notifications',
        label: 'Notifications',
        path: '/volundr/notifications',
        useCount: useUnreadNotificationCount,
      },
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
    expect(paths).toContain('/volundr/notifications');
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

  it('sends the plugin root to the forge', () => {
    expect(redirectTarget(rootBeforeLoad())).toBe('/volundr/forge');
  });
});

describe('old session links', () => {
  it('open the session on the Sessions page, keeping its anchor and query', () => {
    const routes = volundrPlugin.routes?.(createRootRoute()) ?? [];
    const old = routes.find((route) => route.options.path === '/volundr/session/$sessionId');
    const beforeLoad = (old?.options as { beforeLoad: (ctx: unknown) => void }).beforeLoad;

    let target: { to: string; params: unknown; hash: string; search: unknown } | undefined;
    try {
      beforeLoad({
        params: { sessionId: 'review' },
        location: { search: { instance_id: 'forge-east' }, hash: 'notification-n-1' },
      });
    } catch (thrown) {
      target = (thrown as { options: typeof target }).options;
    }

    expect(target).toMatchObject({
      to: '/volundr/sessions/$sessionId',
      params: { sessionId: 'review' },
      search: { instance_id: 'forge-east' },
      hash: 'notification-n-1',
    });
  });
});
