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
  it('shows Work and Workflows in Simple mode', () => {
    expect(tingPlugin.simple).toMatchObject({
      tabs: ['work', 'workflows', 'builder'],
      title: 'Ting',
      subtitle: 'run and oversee work',
    });
    expect(tingPlugin.tabs.map((tab) => tab.id)).toEqual(['work', 'workflows', 'builder']);
  });

  it('routes Work and the builder separately from the workflows catalog', () => {
    expect(routeFor('/ting/work')).toBeDefined();
    expect(routeFor('/ting/work/$workId')).toBeDefined();
    expect(routeFor('/ting/workflows')).toBeDefined();
    expect(routeFor('/ting/workflows/build')).toBeDefined();
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
