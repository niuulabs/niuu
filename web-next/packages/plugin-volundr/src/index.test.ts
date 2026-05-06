import { createRootRoute } from '@tanstack/react-router';
import { describe, expect, it } from 'vitest';
import { volundrPlugin } from './index';

describe('volundrPlugin', () => {
  it('opens on sessions and keeps Forge at the end of the tab list', () => {
    expect(volundrPlugin.tabs).toEqual([
      { id: 'sessions', label: 'Sessions', path: '/volundr' },
      { id: 'templates', label: 'Templates', path: '/volundr/templates' },
      { id: 'credentials', label: 'Credentials', path: '/volundr/credentials' },
      { id: 'clusters', label: 'Clusters', path: '/volundr/clusters' },
      { id: 'forge', label: 'Forge', path: '/volundr/forge' },
    ]);
  });

  it('routes the plugin root to sessions while keeping Forge available explicitly', () => {
    const rootRoute = createRootRoute();
    const routes = volundrPlugin.routes?.(rootRoute) ?? [];
    const paths = routes.map((route) => route.options.path);

    expect(paths).toContain('/volundr');
    expect(paths).toContain('/volundr/forge');
    expect(paths).toContain('/volundr/sessions');
  });
});
