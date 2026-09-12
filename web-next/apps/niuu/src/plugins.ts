import { useEffect } from 'react';
import { createRoute } from '@tanstack/react-router';
import { useAuth } from '@niuulabs/auth';
import { loginPlugin } from '@niuulabs/plugin-login';
import { setupPlugin } from '@niuulabs/plugin-setup';
import { definePlugin, type NiuuConfig, type PluginDescriptor } from '@niuulabs/plugin-sdk';
import { SettingsPage } from './SettingsPage';

function LogoutRoute() {
  const { logout } = useAuth();

  useEffect(() => {
    logout();
  }, [logout]);

  return null;
}

const settingsPlugin = definePlugin({
  id: 'settings',
  rune: '\u2699',
  title: 'Settings',
  subtitle: 'configuration',
  position: 'bottom',
  routes: (rootRoute) => [
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/settings',
      component: SettingsPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/settings/$providerId',
      component: SettingsPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/settings/$providerId/$sectionId',
      component: SettingsPage,
    }),
  ],
});

const logoutPlugin = definePlugin({
  id: 'logout',
  rune: '\u23fb',
  title: 'Sign out',
  subtitle: 'end session',
  position: 'bottom',
  routes: (rootRoute) => [
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/logout',
      component: LogoutRoute,
    }),
  ],
});

const pluginLoaders: Record<string, () => Promise<PluginDescriptor>> = {
  login: async () => loginPlugin,
  setup: async () => setupPlugin,
  volundr: async () => {
    const [module] = await Promise.all([
      import('@niuulabs/plugin-volundr'),
      import('@niuulabs/plugin-volundr/styles.css'),
      import('@niuulabs/plugin-volundr/index.css'),
    ]);
    return module.volundrPlugin;
  },
  ting: async () => {
    const [module] = await Promise.all([
      import('@niuulabs/plugin-ting'),
      import('@niuulabs/plugin-ting/styles.css'),
      import('@niuulabs/plugin-ting/index.css'),
    ]);
    return module.tingPlugin;
  },
  ravn: async () => {
    const [module] = await Promise.all([
      import('@niuulabs/plugin-ravn'),
      import('@niuulabs/plugin-ravn/styles.css'),
      import('@niuulabs/plugin-ravn/index.css'),
    ]);
    return module.ravnPlugin;
  },
  mimir: async () => {
    const [module] = await Promise.all([
      import('@niuulabs/plugin-mimir'),
      import('@niuulabs/plugin-mimir/styles.css'),
      import('@niuulabs/plugin-mimir/index.css'),
    ]);
    return module.mimirPlugin;
  },
  valkyrie: async () => {
    const [module] = await Promise.all([
      import('@niuulabs/plugin-valkyrie'),
      import('@niuulabs/plugin-valkyrie/styles.css'),
      import('@niuulabs/plugin-valkyrie/index.css'),
    ]);
    return module.valkyriePlugin;
  },
  observatory: async () => {
    const [module] = await Promise.all([
      import('@niuulabs/plugin-observatory'),
      import('@niuulabs/plugin-observatory/styles.css'),
      import('@niuulabs/plugin-observatory/index.css'),
    ]);
    return module.observatoryPlugin;
  },
  bifrost: async () => {
    const [module] = await Promise.all([import('@niuulabs/plugin-bifrost/plugin')]);
    return module.bifrostPlugin;
  },
  guild: async () => (await import('./guild')).guildPlugin,
  settings: async () => settingsPlugin,
  logout: async () => logoutPlugin,
};

/** Load only operator-enabled modules, with their styles, preserving configured shell ordering. */
export async function loadEnabledPlugins(config: NiuuConfig): Promise<PluginDescriptor[]> {
  return Promise.all(
    Object.entries(pluginLoaders)
      .filter(([id]) => config.plugins[id]?.enabled !== false)
      .map(([, load]) => load()),
  );
}
