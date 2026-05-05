import { describe, it, expect, vi } from 'vitest';
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
});
