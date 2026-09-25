import { afterEach, describe, expect, it } from 'vitest';
import { createRootRoute } from '@tanstack/react-router';
import { UI_MODE_STORAGE_KEY } from '@niuulabs/shell';
import { ravnPlugin } from './index';

function routeFor(path: string) {
  const routes = ravnPlugin.routes?.(createRootRoute()) ?? [];
  return routes.find((route) => route.options.path === path);
}

afterEach(() => localStorage.clear());

describe('ravnPlugin', () => {
  it('offers the Residents board only while Simple mode is on', () => {
    expect(ravnPlugin.tabs).toContainEqual({
      id: 'residents',
      label: 'Residents',
      path: '/ravn/residents',
      simpleOnly: true,
    });
    expect(ravnPlugin.simple).toMatchObject({
      tabs: ['residents', 'personas'],
      title: 'Residents',
      subtitle: 'who keeps what',
    });
  });

  it('has two advanced tabs: the ravens workbench and the persona library', () => {
    expect(ravnPlugin.tabs?.filter((tab) => !tab.simpleOnly).map((tab) => tab.path)).toEqual([
      '/ravn',
      '/ravn/personas',
    ]);
  });

  it('registers the workbench, library and Residents routes plus the old addresses', () => {
    const routes = ravnPlugin.routes?.(createRootRoute()) ?? [];
    const paths = routes.map((route) => route.options.path);
    expect(paths).toEqual([
      '/ravn',
      '/ravn/residents',
      '/ravn/personas',
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

  it('opens one ravn on the workbench even in Simple mode', () => {
    localStorage.setItem(UI_MODE_STORAGE_KEY, 'simple');
    const beforeLoad = routeFor('/ravn')?.options.beforeLoad as (ctx: unknown) => void;
    expect(beforeLoad({ location: { search: { ravn: 'r-1' } } })).toBeUndefined();
    expect(beforeLoad({ location: { search: { deploy: 'reviewer' } } })).toBeUndefined();
  });

  it('sends the plugin root to the Residents board in Simple mode', () => {
    localStorage.setItem(UI_MODE_STORAGE_KEY, 'simple');
    const beforeLoad = routeFor('/ravn')?.options.beforeLoad;
    expect(() =>
      (beforeLoad as (ctx: unknown) => void)({ location: { search: {} } }),
    ).toThrowError();
  });

  it('keeps the workbench at the plugin root in Advanced mode', () => {
    localStorage.setItem(UI_MODE_STORAGE_KEY, 'advanced');
    const beforeLoad = routeFor('/ravn')?.options.beforeLoad;
    expect((beforeLoad as (ctx: unknown) => void)({ location: { search: {} } })).toBeUndefined();
  });
});
