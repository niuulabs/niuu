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

  it('registers the Residents route alongside the fleet ones', () => {
    const routes = ravnPlugin.routes?.(createRootRoute()) ?? [];
    const paths = routes.map((route) => route.options.path);
    expect(paths).toEqual([
      '/ravn',
      '/ravn/residents',
      '/ravn/ravens',
      '/ravn/personas',
      '/ravn/sessions',
      '/ravn/budget',
    ]);
  });

  it('sends the plugin root to the Residents board in Simple mode', () => {
    localStorage.setItem(UI_MODE_STORAGE_KEY, 'simple');
    const beforeLoad = routeFor('/ravn')?.options.beforeLoad;
    expect(() =>
      (beforeLoad as (ctx: unknown) => void)({ location: { search: {} } }),
    ).toThrowError();
  });

  it('keeps the fleet overview at the plugin root in Advanced mode', () => {
    localStorage.setItem(UI_MODE_STORAGE_KEY, 'advanced');
    const beforeLoad = routeFor('/ravn')?.options.beforeLoad;
    expect((beforeLoad as (ctx: unknown) => void)({ location: { search: {} } })).toBeUndefined();
  });
});
