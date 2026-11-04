import { describe, expect, it } from 'vitest';
import { createRootRoute } from '@tanstack/react-router';
import { ravnPlugin } from './index';

function routeFor(path: string) {
  const routes = ravnPlugin.routes?.(createRootRoute()) ?? [];
  return routes.find((route) => route.options.path === path);
}

describe('ravnPlugin', () => {
  it('has two tabs: the ravens workbench and the persona library', () => {
    expect(ravnPlugin.tabs?.map((tab) => tab.path)).toEqual(['/ravn', '/ravn/personas']);
  });

  it('registers the workbench and library routes plus the old addresses', () => {
    const routes = ravnPlugin.routes?.(createRootRoute()) ?? [];
    const paths = routes.map((route) => route.options.path);
    expect(paths).toEqual([
      '/ravn',
      '/ravn/personas',
      '/ravn/residents',
      '/ravn/ravens',
      '/ravn/sessions',
      '/ravn/budget',
    ]);
  });

  it('sends the old fleet page to the workbench with its query intact', () => {
    const beforeLoad = routeFor('/ravn/ravens')?.options.beforeLoad as (ctx: unknown) => void;
    let thrown: unknown;
    try {
      beforeLoad({ location: { search: { ravn: 'r-1' } } });
    } catch (error) {
      thrown = error;
    }
    expect(thrown).toMatchObject({ options: { to: '/ravn', search: { ravn: 'r-1' } } });
  });

  it('sends an old conversation link to the ravn it belongs to', () => {
    const beforeLoad = routeFor('/ravn/sessions')?.options.beforeLoad as (ctx: unknown) => void;
    let thrown: unknown;
    try {
      beforeLoad({ location: { search: { session: 's-1', ravn_id: 'r-1', instance_id: 'i-1' } } });
    } catch (error) {
      thrown = error;
    }
    expect(thrown).toMatchObject({
      options: {
        to: '/ravn',
        search: { ravn: 'r-1', instance_id: 'i-1', tab: 'chat', session: 's-1' },
      },
    });
  });

  it('sends the old budget page to the usage tab', () => {
    const beforeLoad = routeFor('/ravn/budget')?.options.beforeLoad as (ctx: unknown) => void;
    let thrown: unknown;
    try {
      beforeLoad({ location: { search: {} } });
    } catch (error) {
      thrown = error;
    }
    expect(thrown).toMatchObject({ options: { to: '/ravn', search: { tab: 'usage' } } });
  });

  it('sends the old residents board to the workbench', () => {
    const beforeLoad = routeFor('/ravn/residents')?.options.beforeLoad;
    let thrown: unknown;
    try {
      (beforeLoad as (ctx: unknown) => void)({ location: { search: {} } });
    } catch (error) {
      thrown = error;
    }
    expect(thrown).toMatchObject({ options: { to: '/ravn' } });
  });
});
