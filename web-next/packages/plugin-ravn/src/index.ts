import { createElement } from 'react';
import { createRoute, redirect } from '@tanstack/react-router';
import { Bot } from 'lucide-react';
import { definePlugin } from '@niuulabs/plugin-sdk';
import { readUiMode } from '@niuulabs/shell';
import { ResidentsPage } from './ui/ResidentsPage';
import { RavnWorkbench } from './ui/workbench/RavnWorkbench';
import { PersonaLibrary } from './ui/library/PersonaLibrary';
import { RavnTopbar } from './ui/RavnTopbar';
import { RavnFooter } from './ui/RavnFooter';
import { legacySessionSearch } from './ui/workbench/legacyRoutes';

export const ravnPlugin = definePlugin({
  id: 'ravn',
  rune: 'R',
  title: 'Ravn',
  subtitle: 'ravens · personas',
  simple: {
    // The agents themselves: residents and the personas they run with.
    tabs: ['residents', 'personas'],
    title: 'Residents',
    subtitle: 'who keeps what',
    icon: createElement(Bot, { size: 17, 'aria-hidden': true }),
  },
  tabs: [
    { id: 'ravens', label: 'Ravens', path: '/ravn' },
    { id: 'residents', label: 'Residents', path: '/ravn/residents', simpleOnly: true },
    { id: 'personas', label: 'Personas', path: '/ravn/personas' },
  ],
  routes: (rootRoute) => [
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ravn',
      // Simple mode's page is the Residents board; a link to one ravn (Talk) still opens it here.
      beforeLoad: ({ location }) => {
        if (readUiMode() !== 'simple') return;
        const search = location.search as Record<string, unknown>;
        if (search.ravn || search.deploy) return;
        throw redirect({ to: '/ravn/residents' as never, search: location.search as never });
      },
      component: RavnWorkbench,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ravn/residents',
      component: ResidentsPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ravn/personas',
      component: PersonaLibrary,
    }),
    // The fleet list, the sessions page and the budget page are now the workbench.
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ravn/ravens',
      beforeLoad: ({ location }) => {
        throw redirect({ to: '/ravn' as never, search: location.search as never });
      },
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ravn/sessions',
      beforeLoad: ({ location }) => {
        throw redirect({
          to: '/ravn' as never,
          search: legacySessionSearch(location.search as Record<string, unknown>) as never,
        });
      },
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ravn/budget',
      beforeLoad: () => {
        throw redirect({ to: '/ravn' as never, search: { tab: 'usage' } as never });
      },
    }),
  ],
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
  createMockWardenStore,
} from './adapters/mock';

// HTTP adapters
export {
  buildRavnPersonaAdapter,
  buildRavnRavenAdapter,
  buildRavnResidentControlAdapter,
  buildRavnSessionAdapter,
  buildRavnTriggerAdapter,
  buildRavnBudgetAdapter,
  buildRavnWardenAdapter,
} from './adapters/http';

// Port interfaces + types
export type {
  IPersonaStore,
  IRavenStream,
  IResidentControl,
  ISessionStream,
  ITriggerStore,
  IBudgetStream,
  IWardenStore,
  PersonaSummary,
  PersonaDetail,
  PersonaCreateRequest,
  PersonaForkRequest,
  PersonaFilter,
  PersonaLLM,
  PersonaProduces,
  PersonaConsumes,
  PersonaFanIn,
  DeployResidentRequest,
  CreateResidentSessionRequest,
  ResidentLifecycleAction,
  ResidentLogEntry,
  ResidentLogPage,
  WardenFeatures,
  WardenDreamSummary,
  WardenRuntime,
  WardenSupervisor,
  WardenOperator,
  WardenListener,
  WardenSummary,
  WardenCreateRequest,
} from './ports';

// Domain types
export {
  ravnStatusSchema,
  ravnSchema,
  residentBackendSchema,
  residentEngineSchema,
  residentCapabilitySchema,
  residentConditionSchema,
  residentEndpointSchema,
  type RavnStatus,
  type Ravn,
  type ResidentBackend,
  type ResidentEngine,
  type ResidentCapability,
  type ResidentCondition,
  type ResidentEndpoint,
  type ResidentDeploymentProfile,
} from './domain/ravn';
export {
  sessionStatusSchema,
  sessionSchema,
  type SessionStatus,
  type Session,
} from './domain/session';
export {
  triggerKindSchema,
  triggerSchema,
  type TriggerKind,
  type Trigger,
  type CreatedTrigger,
} from './domain/trigger';
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

// Widgets and hooks shared with other plugins (Realms composes them)
export {
  ResidentDeployFields,
  selectedResidentProfile,
  targetLabel,
  type ResidentDeployFieldsProps,
  type ResidentMemberDraft,
} from './ui/ResidentDeployFields';
export { ResidentModelSelect } from './ui/ResidentModelSelect';
export { PersonaList, type PersonaListProps } from './ui/PersonaList';
export { ResidentDeployDialog } from './ui/ResidentDeployDialog';
export { ResidentsPage } from './ui/ResidentsPage';
export { PersonaForm, type PersonaFormProps } from './ui/PersonaForm';
export { ResidentLogsView } from './ui/ResidentLogsView';
export { MessageRow } from './ui/MessageRow';
export { TriggersView } from './ui/TriggersView';
export {
  useResidentProfiles,
  useDeployResident,
  useResidentSessions,
  useResidentLogs,
  useCreateResidentSession,
  useResidentLifecycle,
} from './ui/hooks/useResidentControl';
export { useTriggers } from './ui/hooks/useTriggers';
export { useRavnBudget, useFleetBudget } from './ui/hooks/useBudget';
export { usePersonas, useOptionalPersonas } from './ui/usePersonas';
export { useRavens, useRaven } from './ui/hooks/useRavens';
export { groupRavens, ravnStatusToDotState, type GroupKey } from './ui/grouping';
export { dispatchSessionSelection, sessionKey } from './ui/sessionSelection';
export {
  canCreateResidentSession,
  canDeleteResidentSession,
  canListResidentSessions,
  canRestartResident,
  canResumeResident,
  canSuspendResident,
  isResidentRavn,
  isResidentSuspended,
  nameForRavn,
  ravnKey,
  realmSlugForRavn,
} from './domain/residentActions';
