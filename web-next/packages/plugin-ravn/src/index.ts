import { createElement } from 'react';
import { createRoute, redirect } from '@tanstack/react-router';
import { AgentsIcon } from '@niuulabs/ui';
import { definePlugin } from '@niuulabs/plugin-sdk';
import { PersonaLibrary } from './ui/library/PersonaLibrary';

export const ravnPlugin = definePlugin({
  id: 'ravn',
  rune: 'R',
  icon: createElement(AgentsIcon),
  title: 'Agents',
  subtitle: 'who does the work',
  tabs: [{ id: 'personas', label: 'Personas', path: '/ravn/personas' }],
  routes: (rootRoute) => [
    // The plugin's base route lands on the persona library.
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ravn',
      beforeLoad: ({ location }) => {
        throw redirect({ to: '/ravn/personas' as never, search: location.search as never });
      },
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ravn/personas',
      component: PersonaLibrary,
    }),
  ],
});

// Mock adapters
export { createMockPersonaStore, createMockTriggerStore } from './adapters/mock';

// HTTP adapters
export { buildRavnPersonaAdapter, buildRavnTriggerAdapter } from './adapters/http';

// Port interfaces + types
export type {
  IPersonaStore,
  ITriggerStore,
  PersonaSummary,
  PersonaDetail,
  PersonaCreateRequest,
  PersonaForkRequest,
  PersonaFilter,
  PersonaLLM,
  PersonaProduces,
  PersonaConsumes,
  PersonaFanIn,
} from './ports';

// Domain types
export {
  triggerKindSchema,
  triggerSchema,
  type TriggerKind,
  type Trigger,
  type CreatedTrigger,
} from './domain/trigger';
