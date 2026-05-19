import { createRootRoute } from '@tanstack/react-router';
import { describe, expect, it } from 'vitest';
import { volundrPlugin } from './index';

describe('volundrPlugin', () => {
  it('keeps Forge at the beginning of the tab list while sessions use their dedicated route', () => {
    expect(volundrPlugin.tabs).toEqual([
      { id: 'forge', label: 'Forge', path: '/volundr/forge' },
      { id: 'sessions', label: 'Sessions', path: '/volundr/sessions' },
      { id: 'templates', label: 'Templates', path: '/volundr/templates' },
    ]);
  });

  it('routes the plugin root to Forge while keeping the sessions shell and legacy redirects available', () => {
    const rootRoute = createRootRoute();
    const routes = volundrPlugin.routes?.(rootRoute) ?? [];
    const paths = routes.map((route) => route.options.path);

    expect(paths).toContain('/volundr');
    expect(paths).toContain('/volundr/forge');
    expect(paths).toContain('/volundr/sessions');
    expect(paths).toContain('/volundr/sessions/$sessionId');
    expect(paths).toContain('/volundr/credentials');
    expect(paths).toContain('/volundr/clusters');
  });
});
