import { createRootRoute } from '@tanstack/react-router';
import { describe, expect, it } from 'vitest';
import { valkyriePlugin } from './index';

describe('valkyriePlugin', () => {
  it('centers the plugin on the resident console', () => {
    expect(valkyriePlugin.tabs).toEqual([
      { id: 'console', label: 'Console', rune: 'ᛒ', path: '/valkyrie' },
      { id: 'activity', label: 'Activity', rune: '↔', path: '/valkyrie/activity' },
      { id: 'fleet', label: 'Fleet', rune: 'ᛗ', path: '/valkyrie/fleet' },
      { id: 'inbox', label: 'Inbox', rune: '◇', path: '/valkyrie/inbox' },
    ]);
  });

  it('keeps inbox and legacy routes available', () => {
    const rootRoute = createRootRoute();
    const routes = valkyriePlugin.routes?.(rootRoute) ?? [];
    const paths = routes.map((route) => route.options.path);

    expect(paths).toContain('/valkyrie');
    expect(paths).toContain('/valkyrie/fleet');
    expect(paths).toContain('/valkyrie/inbox');
    expect(paths).toContain('/valkyrie/activity');
    for (const legacy of [
      '/valkyrie/console',
      '/valkyrie/topology',
      '/valkyrie/lineage',
      '/valkyrie/learning',
      '/valkyrie/huddles',
      '/valkyrie/autonomy',
      // The retired Realms tab redirects to the console.
      '/valkyrie/realms',
      '/valkyries',
      '/valkyries/learning',
    ]) {
      expect(paths).toContain(legacy);
    }
  });
});
