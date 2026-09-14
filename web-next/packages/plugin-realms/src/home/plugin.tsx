import { createRoute } from '@tanstack/react-router';
import { House } from 'lucide-react';
import { definePlugin } from '@niuulabs/plugin-sdk';
import { HomePage } from './HomePage';

/**
 * Home is the Simple-mode landing page. It exists only in Simple mode — Advanced
 * users land on the Forge dashboard, which is where they were before this page.
 */
export const homePlugin = definePlugin({
  id: 'home',
  rune: 'ᚺ',
  title: 'Home',
  subtitle: 'what do you want to do',
  simple: {
    // The place you come back to.
    icon: <House size={17} aria-hidden="true" />,
    only: true,
    landing: true,
  },
  routes: (rootRoute) => [
    createRoute({ getParentRoute: () => rootRoute, path: '/home', component: HomePage }),
  ],
});
