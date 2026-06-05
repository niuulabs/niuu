import { createRoute } from '@tanstack/react-router';
import { definePlugin } from '@niuulabs/plugin-sdk';
import { ValkyriePage } from './ui/ValkyriePage';
import { ValkyrieTopbar } from './ui/ValkyrieTopbar';

export const valkyriePlugin = definePlugin({
  id: 'valkyrie',
  rune: 'ᛒ',
  title: 'Valkyrie',
  subtitle: 'resident operators',
  tabs: [
    { id: 'console', label: 'Console', rune: '◇', path: '/valkyries' },
    { id: 'topology', label: 'Topology', rune: 'ᛗ', path: '/valkyries/topology' },
    { id: 'lineage', label: 'Lineage', rune: '↔', path: '/valkyries/lineage' },
    { id: 'learning', label: 'Learning', rune: 'ᛗ', path: '/valkyries/learning' },
    { id: 'huddles', label: 'Huddles', rune: '†', path: '/valkyries/huddles' },
    { id: 'autonomy', label: 'Autonomy', rune: '§', path: '/valkyries/autonomy' },
  ],
  routes: (rootRoute) => [
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyries',
      component: () => <ValkyriePage defaultView="console" />,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyries/topology',
      component: () => <ValkyriePage defaultView="topology" />,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyries/lineage',
      component: () => <ValkyriePage defaultView="lineage" />,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyries/learning',
      component: () => <ValkyriePage defaultView="learning" />,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyries/huddles',
      component: () => <ValkyriePage defaultView="huddles" />,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyries/autonomy',
      component: () => <ValkyriePage defaultView="autonomy" />,
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
  IValkyrieService,
  IValkyrieSignalStream,
  LearningDecisionRequest,
  ValkyrieSignalListener,
} from './ports';
