import { createRootRoute } from '@tanstack/react-router';
import { describe, expect, it } from 'vitest';
import { valkyriePlugin } from './index';

describe('valkyriePlugin', () => {
  it('uses the singular Valkyrie path as the canonical UI entrypoint', () => {
    expect(valkyriePlugin.tabs).toEqual([
      { id: 'console', label: 'Console', rune: '◇', path: '/valkyrie' },
      { id: 'topology', label: 'Topology', rune: 'ᛗ', path: '/valkyrie/topology' },
      { id: 'lineage', label: 'Lineage', rune: '↔', path: '/valkyrie/lineage' },
      { id: 'learning', label: 'Learning', rune: 'ᛗ', path: '/valkyrie/learning' },
      { id: 'huddles', label: 'Huddles', rune: '†', path: '/valkyrie/huddles' },
      { id: 'autonomy', label: 'Autonomy', rune: '§', path: '/valkyrie/autonomy' },
    ]);
  });

  it('keeps plural Valkyries routes as compatibility redirects', () => {
    const rootRoute = createRootRoute();
    const routes = valkyriePlugin.routes?.(rootRoute) ?? [];
    const paths = routes.map((route) => route.options.path);

    expect(paths).toContain('/valkyrie');
    expect(paths).toContain('/valkyrie/topology');
    expect(paths).toContain('/valkyrie/lineage');
    expect(paths).toContain('/valkyrie/learning');
    expect(paths).toContain('/valkyrie/huddles');
    expect(paths).toContain('/valkyrie/autonomy');
    expect(paths).toContain('/valkyries');
    expect(paths).toContain('/valkyries/topology');
    expect(paths).toContain('/valkyries/lineage');
    expect(paths).toContain('/valkyries/learning');
    expect(paths).toContain('/valkyries/huddles');
    expect(paths).toContain('/valkyries/autonomy');
  });
});
