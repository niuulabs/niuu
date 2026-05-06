import { createRoute } from '@tanstack/react-router';
import { loginPlugin } from '@niuulabs/plugin-login';
import { ravnPlugin } from '@niuulabs/plugin-ravn';
import { mimirPlugin } from '@niuulabs/plugin-mimir';
import { observatoryPlugin } from '@niuulabs/plugin-observatory';
import { tyrPlugin } from '@niuulabs/plugin-tyr';
import { volundrPlugin } from '@niuulabs/plugin-volundr';
import { definePlugin, type PluginDescriptor } from '@niuulabs/plugin-sdk';
import { SettingsPage } from './SettingsPage';

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

export const plugins: PluginDescriptor[] = [
  loginPlugin,
  volundrPlugin,
  tyrPlugin,
  mimirPlugin,
  ravnPlugin,
  observatoryPlugin,
  settingsPlugin,
];
