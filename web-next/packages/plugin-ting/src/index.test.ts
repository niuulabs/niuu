import { createRootRoute } from '@tanstack/react-router';
import { describe, expect, it } from 'vitest';
import { tingPlugin } from './index';

const rootRoute = createRootRoute();
const routes = tingPlugin.routes?.(rootRoute) ?? [];

function routeFor(path: string) {
  const route = routes.find((candidate) => candidate.options.path === path);
  if (!route) throw new Error(`no route for ${path}`);
  return route;
}

describe('tingPlugin descriptor', () => {
  it('is called Work, with its three tabs', () => {
    expect(tingPlugin).toMatchObject({ title: 'Work', subtitle: 'run and oversee work' });
    expect(tingPlugin.tabs.map((tab) => tab.id)).toEqual(['work', 'artifacts', 'workflows']);
  });

  it('routes Work and the one Workflows page', () => {
    expect(routeFor('/ting/work')).toBeDefined();
    expect(routeFor('/ting/work/$workId')).toBeDefined();
    expect(routeFor('/ting/workflows')).toBeDefined();
  });

  it('sends old builder links to the workflow on the Workflows page', () => {
    const beforeLoad = routeFor('/ting/workflows/build').options.beforeLoad as (context: {
      search: Record<string, unknown>;
    }) => void;
    const redirectOf = (search: Record<string, unknown>) => {
      try {
        beforeLoad({ search });
      } catch (error) {
        return (error as { options?: { to?: string; search?: unknown } }).options;
      }
      throw new Error('expected a redirect');
    };

    expect(redirectOf({ id: 'wf-1', version: '1.0.0' })).toMatchObject({
      to: '/ting/workflows',
      search: { workflow: 'wf-1', version: '1.0.0' },
    });
    expect(redirectOf({})).toMatchObject({ to: '/ting/workflows', search: {} });
  });

  it('sends /ting to Work in every mode', () => {
    const beforeLoad = routeFor('/ting').options.beforeLoad as () => void;
    let thrown: unknown;
    try {
      beforeLoad();
    } catch (error) {
      thrown = error;
    }
    expect((thrown as { options?: { to?: string } })?.options?.to).toBe('/ting/work');
  });
});
