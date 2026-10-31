import { createRoute } from '@tanstack/react-router';
import { HomeIcon } from '@niuulabs/ui';
import { definePlugin } from '@niuulabs/plugin-sdk';
import { HomePage } from './HomePage';

/**
 * Home is the Simple-mode landing page. It exists only in Simple mode — Advanced
 * users land on the Forge dashboard, which is where they were before this page.
 */
export const homePlugin = definePlugin({
  id: 'home',
  rune: 'H',
  // The place you come back to.
  icon: <HomeIcon />,
  title: 'Home',
  subtitle: 'what do you want to do',
  simple: {
    only: true,
    landing: true,
  },
  routes: (rootRoute) => [
    createRoute({ getParentRoute: () => rootRoute, path: '/home', component: HomePage }),
  ],
});
