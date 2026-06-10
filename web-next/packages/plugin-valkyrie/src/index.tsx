import { createRoute, redirect } from '@tanstack/react-router';
import { definePlugin } from '@niuulabs/plugin-sdk';
import { ValkyriePage } from './ui/ValkyriePage';
import { ValkyrieTopbar } from './ui/ValkyrieTopbar';

const createLegacyRedirect = (to: string) => ({ location }: { location: { search: unknown } }) => {
  throw redirect({
    to: to as never,
    search: location.search as never,
  });
};

export const valkyriePlugin = definePlugin({
  id: 'valkyrie',
  rune: 'ᛒ',
  title: 'Valkyrie',
  subtitle: 'resident operators',
  tabs: [
    { id: 'console', label: 'Console', rune: '◇', path: '/valkyrie' },
    { id: 'topology', label: 'Topology', rune: 'ᛗ', path: '/valkyrie/topology' },
    { id: 'lineage', label: 'Lineage', rune: '↔', path: '/valkyrie/lineage' },
    { id: 'learning', label: 'Learning', rune: 'ᛗ', path: '/valkyrie/learning' },
    { id: 'huddles', label: 'Huddles', rune: '†', path: '/valkyrie/huddles' },
    { id: 'autonomy', label: 'Autonomy', rune: '§', path: '/valkyrie/autonomy' },
  ],
  routes: (rootRoute) => [
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyrie',
      component: () => <ValkyriePage defaultView="console" />,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyrie/topology',
      component: () => <ValkyriePage defaultView="topology" />,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyrie/lineage',
      component: () => <ValkyriePage defaultView="lineage" />,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyrie/learning',
      component: () => <ValkyriePage defaultView="learning" />,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyrie/huddles',
      component: () => <ValkyriePage defaultView="huddles" />,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyrie/autonomy',
      component: () => <ValkyriePage defaultView="autonomy" />,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyries',
      beforeLoad: createLegacyRedirect('/valkyrie'),
      component: () => null,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyries/topology',
      beforeLoad: createLegacyRedirect('/valkyrie/topology'),
      component: () => null,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyries/lineage',
      beforeLoad: createLegacyRedirect('/valkyrie/lineage'),
      component: () => null,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyries/learning',
      beforeLoad: createLegacyRedirect('/valkyrie/learning'),
      component: () => null,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyries/huddles',
      beforeLoad: createLegacyRedirect('/valkyrie/huddles'),
      component: () => null,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyries/autonomy',
      beforeLoad: createLegacyRedirect('/valkyrie/autonomy'),
      component: () => null,
    }),
  ],
  topbarRight: () => <ValkyrieTopbar />,
});

export { createMockValkyrieService, createMockValkyrieSignalStream } from './adapters/mock';
export { buildValkyrieHttpAdapter, buildValkyrieSignalSseStream } from './adapters/http';
export { useValkyrieDashboard, useValkyrieSignals } from './application/useValkyrieDashboard';
export { selectEnvironmentSlice, selectFlockLearnings } from './application/selectors';
export { normalizeValkyrieSignalEvent } from './domain';
export type {
  ActionRecord,
  AutonomyMode,
  CourtDecision,
  EnvironmentHealth,
  EnvironmentKind,
  EnvironmentSignal,
  EnvironmentSummary,
  FlockSummary,
  HuddleMessage,
  HuddleSummary,
  JudgmentRecord,
  LearningRecord,
  LearningScope,
  LearningStatus,
  OperationalState,
  SignalSeverity,
  SignalStatus,
  ValkyrieDashboard,
  ValkyrieEnvironmentTelemetry,
  ValkyrieLlmTelemetry,
  ValkyriePollTelemetry,
  ValkyrieResident,
  ValkyrieSignalEvent,
  ValkyrieRuntimeTelemetry,
  ValkyrieTelemetry,
  ValkyrieTelemetryTotals,
  WakefulnessState,
} from './domain';
export type {
  AutonomyUpdateRequest,
  HuddleSendRequest,
  HuddleJoinRequest,
  IValkyrieService,
  IValkyrieSignalStream,
  LearningDecisionRequest,
  ValkyrieSignalListener,
} from './ports';
