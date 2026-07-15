import { createRoute, redirect } from '@tanstack/react-router';
import { definePlugin } from '@niuulabs/plugin-sdk';
import { ActivityPage } from './ui/ActivityPage';
import { InboxPage } from './ui/InboxPage';
import { ValkyrieConsolePage } from './ui/ValkyrieConsolePage';
import { ValkyrieTopbar } from './ui/ValkyrieTopbar';

const LEGACY_PATHS = [
  '/valkyrie/console',
  '/valkyrie/topology',
  '/valkyrie/lineage',
  '/valkyrie/learning',
  '/valkyrie/huddles',
  '/valkyrie/autonomy',
  // Realm governance moved onto the console's Authority & autonomy panel.
  '/valkyrie/realms',
  // The fleet view is retired: per-resident autonomy lives on the console,
  // and the topbar picks the resident. The path redirects to the console.
  '/valkyrie/fleet',
  '/valkyries',
  '/valkyries/fleet',
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
  createMockValkyrieSkillsService,
  createSeedDecisions,
  createSeedLearnedSkills,
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
  buildValkyrieSkillsHttpAdapter,
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
export { useValkyrieSkill, useValkyrieSkills } from './application/useValkyrieSkills';
export {
  useLearning,
  useReviseLearning,
  useSendLearningFeedback,
} from './application/useLearnings';
export { useDecideReview, useReviewList, useReviewSummary } from './application/useReviews';
export {
  adjacentLearningScopes,
  autonomyModeForLevel,
  collapseDecisionsByCorrelation,
  decisionHasRealAction,
  decisionHeadline,
  decisionNeedsApproval,
  decisionSkillName,
  decisionSubject,
  grantWorkflowName,
  isToolBuilderWorkflow,
  latestBuildGrant,
  learningFeedbackVerdictLabel,
  realmSlugForEnvironment,
  normalizeReviewItem,
  referencedSkillName,
  reviewArtifactEvidence,
  reviewEffectStatement,
  reviewPolicyFindings,
  ACTIVITY_STORY_LIMIT,
  BUILD_ACTION_CLASS,
  LEARNING_FEEDBACK_VERDICTS,
  LEARNING_SCOPE_ORDER,
  LIST_LIMIT,
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
  GroupedDecision,
  HistoryPage,
  LearnedSkillRecord,
  LearnedSkillSummary,
  LearningFeedback,
  LearningFeedbackVerdict,
  LearningRecord,
  LearningScope,
  LearningStatus,
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
  IValkyrieSkillsService,
  LearningFeedbackInput,
  LearningRevisionInput,
  LearningRevisionResult,
  ReviewDecisionRequest,
  ReviewListFilters,
  ReviewSummaryFilters,
  SignalHistoryFilters,
  TrustGrantCreate,
} from './ports';
