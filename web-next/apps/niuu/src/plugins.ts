import { createElement, useEffect } from 'react';
import { createRoute } from '@tanstack/react-router';
import { useAuth } from '@niuulabs/auth';
import { bifrostPlugin } from '@niuulabs/plugin-bifrost/plugin';
import { loginPlugin } from '@niuulabs/plugin-login';
import { ravnPlugin } from '@niuulabs/plugin-ravn';
import { mimirPlugin } from '@niuulabs/plugin-mimir';
import { observatoryPlugin } from '@niuulabs/plugin-observatory';
import { tingPlugin } from '@niuulabs/plugin-ting';
import { valkyriePlugin } from '@niuulabs/plugin-valkyrie';
import { volundrPlugin } from '@niuulabs/plugin-volundr';
import { definePlugin, type PluginDescriptor } from '@niuulabs/plugin-sdk';
import { GuildPage } from './GuildPage';
import { SettingsPage } from './SettingsPage';

function GuildTopbar() {
  return createElement(
    'button',
    {
      type: 'button',
      onClick: () => {
        window.dispatchEvent(new Event('guild:open-register'));
      },
      className:
        'niuu:inline-flex niuu:items-center niuu:gap-2 niuu:rounded-lg niuu:border niuu:border-brand/35 niuu:bg-brand/12 niuu:px-3 niuu:py-1.5 niuu:text-[12px] niuu:font-medium niuu:text-brand niuu:hover:bg-brand/18',
    },
    '+ register',
  );
}

function LogoutRoute() {
  const { logout } = useAuth();

  useEffect(() => {
    logout();
  }, [logout]);

  return null;
}

const guildPlugin = definePlugin({
  id: 'guild',
  rune: 'G',
  title: 'Guild',
  subtitle: 'runtime registry',
  tabs: [{ id: 'instances', label: 'Instances', path: '/guild' }],
  routes: (rootRoute) => [
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/guild',
      component: GuildPage,
    }),
  ],
  topbarRight: () => createElement(GuildTopbar),
});

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

export const plugins: PluginDescriptor[] = [
  loginPlugin,
  volundrPlugin,
  tingPlugin,
  ravnPlugin,
  mimirPlugin,
  valkyriePlugin,
  observatoryPlugin,
  bifrostPlugin,
  guildPlugin,
  settingsPlugin,
  logoutPlugin,
];
