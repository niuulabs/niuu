import { createRoute } from '@tanstack/react-router';
import { definePlugin } from '@niuulabs/plugin-sdk';
import { BifrostPage } from './ui/BifrostPage';

export const bifrostPlugin = definePlugin({
  id: 'bifrost',
  rune: 'B',
  title: 'Bifröst',
  subtitle: 'llm control plane',
  tabs: [
    { id: 'overview', label: 'Overview', rune: '◎', path: '/bifrost' },
    { id: 'models', label: 'Models', rune: '▦', path: '/bifrost/models' },
    { id: 'providers', label: 'Providers', rune: '⇄', path: '/bifrost/providers' },
    { id: 'usage', label: 'Usage', rune: '≋', path: '/bifrost/usage' },
  ],
  routes: (rootRoute) => [
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/bifrost',
      component: () => <BifrostPage defaultTab="overview" />,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/bifrost/models',
      component: () => <BifrostPage defaultTab="models" />,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/bifrost/providers',
      component: () => <BifrostPage defaultTab="providers" />,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/bifrost/usage',
      component: () => <BifrostPage defaultTab="usage" />,
    }),
  ],
});
