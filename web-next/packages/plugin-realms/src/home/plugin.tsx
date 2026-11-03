import { createRoute } from '@tanstack/react-router';
import { HomeIcon } from '@niuulabs/ui';
import { definePlugin } from '@niuulabs/plugin-sdk';
import { HomePage } from './HomePage';

/** Home: what you want to do, and what is waiting on you. */
export const homePlugin = definePlugin({
  id: 'home',
  rune: 'H',
  // The place you come back to.
  icon: <HomeIcon />,
  title: 'Home',
  subtitle: 'what do you want to do',
  routes: (rootRoute) => [
    createRoute({ getParentRoute: () => rootRoute, path: '/home', component: HomePage }),
  ],
});
