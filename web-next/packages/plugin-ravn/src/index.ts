import { createRoute } from '@tanstack/react-router';
import { definePlugin } from '@niuulabs/plugin-sdk';
import { RavnPage } from './ui/RavnPage';
import { RavensPage } from './ui/RavensPage';
import { PersonasPage } from './ui/PersonasPage';
import { SessionsView } from './ui/SessionsView';
import { BudgetView } from './ui/BudgetView';
import { RavnSubnav } from './ui/RavnSubnav';
import { RavnTopbar } from './ui/RavnTopbar';
import { RavnFooter } from './ui/RavnFooter';

export const ravnPlugin = definePlugin({
  id: 'ravn',
  rune: 'ᚱ',
  title: 'Ravn',
  subtitle: 'personas · ravens · sessions',
  tabs: [
    { id: 'overview', label: 'Overview', path: '/ravn' },
    { id: 'ravens', label: 'Ravens', path: '/ravn/ravens' },
    { id: 'personas', label: 'Personas', path: '/ravn/personas' },
    { id: 'sessions', label: 'Sessions', path: '/ravn/sessions' },
    { id: 'budget', label: 'Budget', path: '/ravn/budget' },
  ],
  routes: (rootRoute) => [
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ravn',
      component: RavnPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ravn/ravens',
      component: RavensPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ravn/personas',
      component: PersonasPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ravn/sessions',
      component: SessionsView,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ravn/budget',
      component: BudgetView,
    }),
  ],
  subnav: () => RavnSubnav(),
  topbarRight: () => RavnTopbar(),
  footer: () => RavnFooter(),
});

// Mock adapters
export {
  createMockPersonaStore,
  createMockRavenStream,
  createMockSessionStream,
  createMockTriggerStore,
  createMockBudgetStream,
} from './adapters/mock';

// HTTP adapters
export {
  buildRavnPersonaAdapter,
  buildRavnRavenAdapter,
  buildRavnSessionAdapter,
  buildRavnTriggerAdapter,
  buildRavnBudgetAdapter,
} from './adapters/http';

// Port interfaces + types
export type {
  IPersonaStore,
  IRavenStream,
  ISessionStream,
  ITriggerStore,
  IBudgetStream,
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
export { ravnStatusSchema, ravnSchema, type RavnStatus, type Ravn } from './domain/ravn';
export {
  sessionStatusSchema,
  sessionSchema,
  type SessionStatus,
  type Session,
} from './domain/session';
export { triggerKindSchema, triggerSchema, type TriggerKind, type Trigger } from './domain/trigger';
export { messageKindSchema, messageSchema, type MessageKind, type Message } from './domain/message';

// Application logic
export {
  classifyBudget,
  budgetRunway,
  budgetRatio,
  type BudgetAttention,
} from './application/budgetAttention';
export {
  applyLogFilter,
  EMPTY_LOG_FILTER,
  type LogEntry,
  type LogFilter,
} from './application/logFilter';
