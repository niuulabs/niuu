import {
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
  type AnyRouter,
} from '@tanstack/react-router';
import type { RouterHistory } from '@tanstack/react-router';
import type { PluginDescriptor } from '@niuulabs/plugin-sdk';
import { ShellLayout } from './ShellLayout';
import { NotFoundPage } from './NotFoundPage';
import { useShellContext } from './ShellContext';

export interface ComposeRouterOptions {
  /** Override the browser history (useful for testing and Storybook). */
  history?: RouterHistory;
}

/**
 * Build a TanStack Router instance from the set of enabled plugins.
 *
 * - Each plugin that exports `routes(rootRoute)` contributes its route tree.
 * - Plugins without `routes` get a default route at `/<id>` that delegates to
 *   their `render()` function (backward-compat for test helpers and legacy plugins).
 * - An index route at `/` redirects to the active plugin (localStorage hint or
 *   the first enabled plugin).
 * - A catch-all not-found component handles unknown paths.
 */
export function composeRouter(
  enabled: PluginDescriptor[],
  options: ComposeRouterOptions = {},
): AnyRouter {
  const rootRoute = createRootRoute({
    component: ShellLayout,
    notFoundComponent: NotFoundPage,
  });

  // System plugins (e.g. login) provide routes but should not be the default
  // redirect destination.
  const navPlugins = enabled.filter((p) => !p.system);

  // Index route: redirect to the stored/default plugin path
  const indexRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/',
    beforeLoad: () => {
      const storedId = typeof window !== 'undefined' ? localStorage.getItem('niuu.active') : null;
      const targetId =
        storedId && navPlugins.some((p) => p.id === storedId)
          ? storedId
          : (navPlugins[0]?.id ?? null);
      if (targetId) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        throw redirect({ to: `/${targetId}` as any });
      }
    },
    component: () => null,
  });

  // Collect routes from each plugin, generating a fallback route when a plugin
  // has no `routes` function (uses `render` for backward compat).
  const pluginRoutes = enabled.flatMap((plugin) => {
    if (plugin.routes) {
      return plugin.routes(rootRoute);
    }

    // Auto-generated fallback route — uses plugin.render(ctx) as its component.
    function RenderFallback() {
      const { ctx } = useShellContext();
      return plugin.render?.(ctx) ?? null;
    }

    return [
      createRoute({
        getParentRoute: () => rootRoute,
        path: `/${plugin.id}`,
        component: RenderFallback,
      }),
    ];
  });

  const routeTree = rootRoute.addChildren([indexRoute, ...pluginRoutes]);

  return createRouter({
    routeTree,
    ...(options.history ? { history: options.history } : {}),
  });
}
