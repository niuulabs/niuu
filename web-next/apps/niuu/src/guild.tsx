import { createElement } from 'react';
import { createRoute } from '@tanstack/react-router';
import { definePlugin } from '@niuulabs/plugin-sdk';
import { GuildPage } from './GuildPage';

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

export const guildPlugin = definePlugin({
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
