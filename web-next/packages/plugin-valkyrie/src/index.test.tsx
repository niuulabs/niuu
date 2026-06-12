import { createRootRoute } from '@tanstack/react-router';
import { describe, expect, it } from 'vitest';
import { valkyriePlugin } from './index';

describe('valkyriePlugin', () => {
  it('centers the plugin on the ODIN review inbox', () => {
    expect(valkyriePlugin.tabs).toEqual([
      { id: 'inbox', label: 'Inbox', rune: '◇', path: '/valkyrie' },
      { id: 'fleet', label: 'Fleet', rune: 'ᛗ', path: '/valkyrie/fleet' },
      { id: 'activity', label: 'Activity', rune: '↔', path: '/valkyrie/activity' },
    ]);
  });

  it('redirects every legacy route to the inbox', () => {
    const rootRoute = createRootRoute();
    const routes = valkyriePlugin.routes?.(rootRoute) ?? [];
    const paths = routes.map((route) => route.options.path);

    expect(paths).toContain('/valkyrie');
    expect(paths).toContain('/valkyrie/fleet');
    expect(paths).toContain('/valkyrie/activity');
    for (const legacy of [
      '/valkyrie/console',
      '/valkyrie/topology',
      '/valkyrie/lineage',
      '/valkyrie/learning',
      '/valkyrie/huddles',
      '/valkyrie/autonomy',
      '/valkyries',
      '/valkyries/learning',
    ]) {
      expect(paths).toContain(legacy);
    }
  });
});
