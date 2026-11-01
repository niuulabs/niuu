import { createRootRoute } from '@tanstack/react-router';
import { describe, expect, it } from 'vitest';
import { valkyriePlugin } from './index';

describe('valkyriePlugin', () => {
  it('centers the plugin on the resident console without a Fleet tab', () => {
    expect(valkyriePlugin.tabs).toEqual([
      { id: 'console', label: 'Console', rune: 'ᛒ', path: '/valkyrie' },
      { id: 'activity', label: 'Activity', rune: '↔', path: '/valkyrie/activity' },
      { id: 'inbox', label: 'Inbox', rune: '◇', path: '/valkyrie/inbox' },
    ]);
    // The retired Fleet tab must not resurface.
    expect(valkyriePlugin.tabs?.some((tab) => tab.id === 'fleet')).toBe(false);
  });

  it('keeps inbox and legacy routes available and retires fleet to a redirect', () => {
    const rootRoute = createRootRoute();
    const routes = valkyriePlugin.routes?.(rootRoute) ?? [];
    const paths = routes.map((route) => route.options.path);

    expect(paths).toContain('/valkyrie');
    expect(paths).toContain('/valkyrie/inbox');
    expect(paths).toContain('/valkyrie/activity');
    // /valkyrie/fleet is still a path — but only as a legacy redirect (below),
    // never a live route with the FleetPage component.
    const fleetRoutes = routes.filter((route) => route.options.path === '/valkyrie/fleet');
    expect(fleetRoutes).toHaveLength(1);
    expect(fleetRoutes[0]?.options.beforeLoad).toBeDefined();
    for (const legacy of [
      '/valkyrie/console',
      '/valkyrie/topology',
      '/valkyrie/lineage',
      '/valkyrie/learning',
      '/valkyrie/huddles',
      '/valkyrie/autonomy',
      // The retired Realms tab redirects to the console.
      '/valkyrie/realms',
      // The retired Fleet tab redirects to the console.
      '/valkyrie/fleet',
      '/valkyries',
      '/valkyries/fleet',
      '/valkyries/learning',
    ]) {
      expect(paths).toContain(legacy);
    }
  });
});
