import { createRoute } from '@tanstack/react-router';
import { definePlugin } from '@niuulabs/plugin-sdk';
import { ValkyriePage } from './ui/ValkyriePage';
import { ValkyrieSubnav } from './ui/ValkyrieSubnav';
import { ValkyrieTopbar } from './ui/ValkyrieTopbar';

export const valkyriePlugin = definePlugin({
  id: 'valkyrie',
  rune: 'V',
  title: 'Valkyrie',
  subtitle: 'environments · flocks · learning',
  tabs: [{ id: 'console', label: 'Console', path: '/valkyries' }],
  routes: (rootRoute) => [
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyries',
      component: ValkyriePage,
    }),
  ],
  subnav: () => <ValkyrieSubnav />,
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
  ValkyrieResident,
  ValkyrieSignalEvent,
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
