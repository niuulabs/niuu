import { ModelsIcon } from '@niuulabs/ui';
import { createRoute } from '@tanstack/react-router';
import { definePlugin } from '@niuulabs/plugin-sdk';
import { BifrostPage } from './ui/BifrostPage';

export const bifrostPlugin = definePlugin({
  id: 'bifrost',
  rune: 'B',
  icon: <ModelsIcon />,
  title: 'AI models',
  subtitle: 'models, providers and usage',
  tabs: [
    { id: 'overview', label: 'Overview', path: '/bifrost' },
    { id: 'models', label: 'Models', path: '/bifrost/models' },
    { id: 'providers', label: 'Providers', path: '/bifrost/providers' },
    { id: 'usage', label: 'Usage', path: '/bifrost/usage' },
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
