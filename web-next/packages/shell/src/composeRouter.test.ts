import { createElement, type ComponentType } from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createMemoryHistory, createRoute } from '@tanstack/react-router';
import { definePlugin } from '@niuulabs/plugin-sdk';
import { composeRouter } from './composeRouter';

// Minimal stub so ShellLayout doesn't need a full DOM environment
vi.mock('./ShellLayout', () => ({ ShellLayout: () => null }));
vi.mock('./NotFoundPage', () => ({ NotFoundPage: () => null }));
vi.mock('./ShellContext', () => ({
  useShellContext: vi.fn(() => ({ ctx: { tweaks: {}, setTweak: vi.fn() } })),
}));

const makePlugin = (id: string, system = false) =>
  definePlugin({
    id,
    rune: 'ᚺ',
    title: id,
    subtitle: id,
    system,
    render: () => null,
  });

describe('composeRouter', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('creates a router with only the index and not-found routes when no plugins are given', () => {
    const router = composeRouter([], {
      history: createMemoryHistory({ initialEntries: ['/'] }),
    });
    // Flat route map should include the root and index
    const paths = Object.keys(router.routesById);
    expect(paths).toContain('__root__');
    expect(paths).toContain('/');
  });

  it('creates a fallback route for each plugin that has no routes() function', () => {
    const router = composeRouter([makePlugin('alpha'), makePlugin('beta')], {
      history: createMemoryHistory({ initialEntries: ['/'] }),
    });
    const paths = Object.keys(router.routesById);
    expect(paths).toContain('/alpha');
    expect(paths).toContain('/beta');
  });

  it('incorporates routes returned by plugin.routes()', () => {
    const withRoutes = definePlugin({
      id: 'gamma',
      rune: 'ᚷ',
      title: 'Gamma',
      subtitle: 'test',
      routes: (root) => [
        createRoute({
          getParentRoute: () => root,
          path: '/gamma',
          component: () => null,
        }),
      ],
    });

    const router = composeRouter([withRoutes], {
      history: createMemoryHistory({ initialEntries: ['/'] }),
    });
    const paths = Object.keys(router.routesById);
    expect(paths).toContain('/gamma');
  });

  it('always includes the root route and an index redirect route', () => {
    const router = composeRouter([makePlugin('x')], {
      history: createMemoryHistory({ initialEntries: ['/'] }),
    });
    expect(router.routesById).toHaveProperty('__root__');
    expect(router.routesById).toHaveProperty('/');
  });

  it('the root route has a notFoundComponent configured', () => {
    const router = composeRouter([], {
      history: createMemoryHistory({ initialEntries: ['/'] }),
    });
    // @ts-expect-error accessing internal options for assertion
    expect(router.routesById['__root__']?.options?.notFoundComponent).toBeDefined();
  });

  it('registers routes for system plugins', () => {
    const login = makePlugin('login', true);
    const router = composeRouter([login], {
      history: createMemoryHistory({ initialEntries: ['/'] }),
    });
    const paths = Object.keys(router.routesById);
    expect(paths).toContain('/login');
  });

  it('system plugin routes appear in the router even when mixed with regular plugins', () => {
    const login = makePlugin('login', true);
    const hello = makePlugin('hello');
    const router = composeRouter([login, hello], {
      history: createMemoryHistory({ initialEntries: ['/'] }),
    });
    const paths = Object.keys(router.routesById);
    expect(paths).toContain('/login');
    expect(paths).toContain('/hello');
  });

  it('redirects the index route to the stored active plugin when it is a navigable plugin', () => {
    window.localStorage.setItem('niuu.active', 'hello');
    const router = composeRouter([makePlugin('login', true), makePlugin('hello')], {
      history: createMemoryHistory({ initialEntries: ['/'] }),
    });

    const beforeLoad = (router.routesById['/'] as { options: { beforeLoad?: () => void } }).options
      .beforeLoad;

    let thrown: unknown;
    try {
      beforeLoad?.();
    } catch (error) {
      thrown = error;
    }

    expect(thrown).toMatchObject({ options: { to: '/hello' } });
  });

  it('redirects the index route to the first navigable plugin when storage is invalid', () => {
    window.localStorage.setItem('niuu.active', 'missing');
    const router = composeRouter([makePlugin('login', true), makePlugin('alpha')], {
      history: createMemoryHistory({ initialEntries: ['/'] }),
    });

    const beforeLoad = (router.routesById['/'] as { options: { beforeLoad?: () => void } }).options
      .beforeLoad;

    let thrown: unknown;
    try {
      beforeLoad?.();
    } catch (error) {
      thrown = error;
    }

    expect(thrown).toMatchObject({ options: { to: '/alpha' } });
  });

  it('does not redirect the index route when only system plugins are enabled', () => {
    window.localStorage.setItem('niuu.active', 'login');
    const router = composeRouter([makePlugin('login', true)], {
      history: createMemoryHistory({ initialEntries: ['/'] }),
    });

    const beforeLoad = (router.routesById['/'] as { options: { beforeLoad?: () => void } }).options
      .beforeLoad;

    expect(() => beforeLoad?.()).not.toThrow();
  });

  it('invokes plugin.render for the generated fallback route component', () => {
    const renderPlugin = vi.fn(() =>
      createElement('div', { 'data-testid': 'fallback' }, 'Fallback route'),
    );
    const plugin = definePlugin({
      id: 'alpha',
      rune: 'ᚨ',
      title: 'Alpha',
      subtitle: 'Alpha',
      render: renderPlugin,
    });

    const router = composeRouter([plugin], {
      history: createMemoryHistory({ initialEntries: ['/alpha'] }),
    });

    const component = (router.routesById['/alpha'] as { options: { component: ComponentType } })
      .options.component;

    render(createElement(component));

    expect(screen.getByTestId('fallback')).toHaveTextContent('Fallback route');
    expect(renderPlugin).toHaveBeenCalledWith({ tweaks: {}, setTweak: expect.any(Function) });
  });

  it('falls back to the first navigable plugin when the stored id belongs to a system plugin', () => {
    window.localStorage.setItem('niuu.active', 'login');
    const router = composeRouter([makePlugin('login', true), makePlugin('alpha')], {
      history: createMemoryHistory({ initialEntries: ['/'] }),
    });

    const beforeLoad = (router.routesById['/'] as { options: { beforeLoad?: () => void } }).options
      .beforeLoad;

    let thrown: unknown;
    try {
      beforeLoad?.();
    } catch (error) {
      thrown = error;
    }

    expect(thrown).toMatchObject({ options: { to: '/alpha' } });
  });

  it('creates a fallback route that renders null when the plugin has no render function', () => {
    const plugin = definePlugin({
      id: 'beta',
      rune: 'ᛒ',
      title: 'Beta',
      subtitle: 'Beta',
    });

    const router = composeRouter([plugin], {
      history: createMemoryHistory({ initialEntries: ['/beta'] }),
    });

    const component = (router.routesById['/beta'] as { options: { component: ComponentType } })
      .options.component;

    const view = render(createElement(component));

    expect(view.container).toBeEmptyDOMElement();
  });

  it('creates a router even when no explicit history override is provided', () => {
    const router = composeRouter([makePlugin('alpha')]);

    expect(router.routesById).toHaveProperty('/alpha');
  });

  it('redirects to the first navigable plugin when no stored plugin is set', () => {
    const router = composeRouter([makePlugin('alpha')], {
      history: createMemoryHistory({ initialEntries: ['/'] }),
    });

    const beforeLoad = (router.routesById['/'] as { options: { beforeLoad?: () => void } }).options
      .beforeLoad;

    let thrown: unknown;
    try {
      beforeLoad?.();
    } catch (error) {
      thrown = error;
    }

    expect(thrown).toMatchObject({ options: { to: '/alpha' } });
  });

  it('uses a null component for the index redirect route', () => {
    const router = composeRouter([makePlugin('alpha')], {
      history: createMemoryHistory({ initialEntries: ['/'] }),
    });
    const component = (router.routesById['/'] as { options: { component: ComponentType } }).options
      .component;

    const view = render(createElement(component));

    expect(view.container).toBeEmptyDOMElement();
  });
});
