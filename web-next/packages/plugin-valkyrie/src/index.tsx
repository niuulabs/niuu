import { createRoute, redirect } from '@tanstack/react-router';
import { definePlugin } from '@niuulabs/plugin-sdk';
import { ActivityPage } from './ui/ActivityPage';
import { FleetPage } from './ui/FleetPage';
import { InboxPage } from './ui/InboxPage';
import { RealmsPage } from './ui/RealmsPage';
import { ValkyrieConsolePage } from './ui/ValkyrieConsolePage';
import { ValkyrieTopbar } from './ui/ValkyrieTopbar';

const LEGACY_PATHS = [
  '/valkyrie/console',
  '/valkyrie/topology',
  '/valkyrie/lineage',
  '/valkyrie/learning',
  '/valkyrie/huddles',
  '/valkyrie/autonomy',
  '/valkyries',
  '/valkyries/topology',
  '/valkyries/lineage',
  '/valkyries/learning',
  '/valkyries/huddles',
  '/valkyries/autonomy',
];

const createLegacyRedirect =
  (to: string) =>
  ({ location }: { location: { search: unknown } }) => {
    throw redirect({
      to: to as never,
      search: location.search as never,
    });
  };

export const valkyriePlugin = definePlugin({
  id: 'valkyrie',
  rune: 'V',
  title: 'Valkyrie',
  subtitle: 'resident telemetry',
  tabs: [
    { id: 'console', label: 'Console', rune: 'ᛒ', path: '/valkyrie' },
    { id: 'activity', label: 'Activity', rune: '↔', path: '/valkyrie/activity' },
    { id: 'fleet', label: 'Fleet', rune: 'ᛗ', path: '/valkyrie/fleet' },
    { id: 'realms', label: 'Realms', rune: 'ᚱ', path: '/valkyrie/realms' },
    { id: 'inbox', label: 'Inbox', rune: '◇', path: '/valkyrie/inbox' },
  ],
  routes: (rootRoute) => [
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyrie',
      component: () => <ValkyrieConsolePage />,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyrie/fleet',
      component: () => <FleetPage />,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyrie/realms',
      component: () => <RealmsPage />,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyrie/inbox',
      component: () => <InboxPage />,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyrie/activity',
      component: () => <ActivityPage />,
    }),
    ...LEGACY_PATHS.map((path) =>
      createRoute({
        getParentRoute: () => rootRoute,
        path,
        beforeLoad: createLegacyRedirect('/valkyrie'),
        component: () => null,
      }),
    ),
  ],
  topbarRight: () => <ValkyrieTopbar />,
});

export {
  createMockOdinReviewService,
  createMockRealmGovernanceService,
  createMockValkyrieService,
  createSeedDecisions,
  createSeedRealms,
  createSeedReviewItems,
  createSeedSignalHistory,
  createSeedSkillStats,
  createSeedToolWorkflows,
  createSeedTrustGrants,
  createSeedValkyrieDashboard,
} from './adapters/mock';
export {
  buildOdinReviewHttpAdapter,
  buildRealmGovernanceHttpAdapter,
  buildValkyrieHttpAdapter,
} from './adapters/http';
export { useUpdateAutonomy, useValkyrieDashboard } from './application/useValkyrieDashboard';
export {
  useCreateTrustGrant,
  useRealms,
  useRealmTrustGrants,
  useToolWorkflows,
} from './application/useRealmGovernance';
export {
  useDecisionDetail,
  useDecisionList,
  useSignalHistory,
  useSkillStats,
} from './application/useValkyrieHistory';
export { useDecideReview, useReviewList, useReviewSummary } from './application/useReviews';
export {
  autonomyModeForLevel,
  grantWorkflowName,
  isToolBuilderWorkflow,
  latestBuildGrant,
  normalizeReviewItem,
  reviewArtifactEvidence,
  reviewEffectStatement,
  reviewPolicyFindings,
  BUILD_ACTION_CLASS,
  TOOL_BUILDER_TAG,
  TRUST_LEVELS,
} from './domain';
export type {
  AutonomyMode,
  DecisionDetail,
  DecisionRecord,
  EnvironmentHealth,
  EnvironmentKind,
  EnvironmentSummary,
  FlockSummary,
  HistoryPage,
  RealmSummary,
  RealmTrustGrant,
  ReviewItem,
  ReviewKind,
  ReviewRiskClass,
  ReviewStatus,
  ReviewSummary,
  SignalHistoryEntry,
  SkillUsageStat,
  TingWorkflowSummary,
  ValkyrieDashboard,
  ValkyrieResident,
  WakefulnessState,
} from './domain';
export type {
  AutonomyUpdateRequest,
  DecisionListFilters,
  IOdinReviewService,
  IRealmGovernanceService,
  IValkyrieService,
  ReviewDecisionRequest,
  ReviewListFilters,
  SignalHistoryFilters,
  TrustGrantCreate,
} from './ports';
