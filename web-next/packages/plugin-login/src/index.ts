import { createRoute } from '@tanstack/react-router';
import { definePlugin } from '@niuulabs/plugin-sdk';
import { LoginPage } from './ui/LoginPage';
import { CallbackPage } from './ui/CallbackPage';

export const loginPlugin = definePlugin({
  id: 'login',
  rune: 'ᚾ',
  title: 'Sign in',
  subtitle: 'authenticate to niuu',
  system: true,
  routes: (rootRoute) => [
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/login',
      component: LoginPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/login/callback',
      component: CallbackPage,
    }),
  ],
});
