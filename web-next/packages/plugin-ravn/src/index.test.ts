import { describe, expect, it } from 'vitest';
import { createRootRoute } from '@tanstack/react-router';
import { ravnPlugin } from './index';

function routeFor(path: string) {
  const routes = ravnPlugin.routes?.(createRootRoute()) ?? [];
  return routes.find((route) => route.options.path === path);
}

describe('ravnPlugin', () => {
  it('has one tab: the persona library', () => {
    expect(ravnPlugin.tabs?.map((tab) => tab.path)).toEqual(['/ravn/personas']);
  });

  it('registers the base route and the persona library route, and nothing else', () => {
    const routes = ravnPlugin.routes?.(createRootRoute()) ?? [];
    expect(routes.map((route) => route.options.path)).toEqual(['/ravn', '/ravn/personas']);
  });

  it('lands the base route on the persona library with its query intact', () => {
    const beforeLoad = routeFor('/ravn')?.options.beforeLoad as (ctx: unknown) => void;
    let thrown: unknown;
    try {
      beforeLoad({ location: { search: { persona: 'reviewer' } } });
    } catch (error) {
      thrown = error;
    }
    expect(thrown).toMatchObject({
      options: { to: '/ravn/personas', search: { persona: 'reviewer' } },
    });
  });
});
