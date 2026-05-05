import { createRoute } from '@tanstack/react-router';
import { definePlugin } from '@niuulabs/plugin-sdk';
import { HelloPage } from './ui/HelloPage';
import { StatusShowcasePage } from './ui/StatusShowcasePage';
import { OverlaysPage } from './ui/OverlaysPage';
import { FormShowcasePage } from './ui/FormShowcasePage';

export const helloPlugin = definePlugin({
  id: 'hello',
  rune: 'ᚺ',
  title: 'Hello',
  subtitle: 'smoke test plugin',
  routes: (rootRoute) => [
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/hello',
      component: HelloPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/hello/status-showcase',
      component: StatusShowcasePage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/overlays',
      component: OverlaysPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/hello/form-showcase',
      component: FormShowcasePage,
    }),
  ],
});

export { createMockHelloService } from './adapters/mock';
export { buildHelloHttpAdapter } from './adapters/http';
export type { IHelloService, Greeting } from './ports';
