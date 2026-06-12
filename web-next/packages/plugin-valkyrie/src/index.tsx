import { createRoute, redirect } from '@tanstack/react-router';
import { definePlugin } from '@niuulabs/plugin-sdk';
import { ActivityPage } from './ui/ActivityPage';
import { FleetPage } from './ui/FleetPage';
import { InboxPage } from './ui/InboxPage';
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
  rune: 'ᛒ',
  title: 'Valkyrie',
  subtitle: 'odin review',
  tabs: [
    { id: 'inbox', label: 'Inbox', rune: '◇', path: '/valkyrie' },
    { id: 'fleet', label: 'Fleet', rune: 'ᛗ', path: '/valkyrie/fleet' },
    { id: 'activity', label: 'Activity', rune: '↔', path: '/valkyrie/activity' },
  ],
  routes: (rootRoute) => [
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyrie',
      component: () => <InboxPage />,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyrie/fleet',
      component: () => <FleetPage />,
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
  createMockValkyrieService,
  createSeedReviewItems,
  createSeedValkyrieDashboard,
} from './adapters/mock';
export { buildOdinReviewHttpAdapter, buildValkyrieHttpAdapter } from './adapters/http';
export { useUpdateAutonomy, useValkyrieDashboard } from './application/useValkyrieDashboard';
export { useDecideReview, useReviewList, useReviewSummary } from './application/useReviews';
export {
  normalizeReviewItem,
  reviewArtifactEvidence,
  reviewEffectStatement,
  reviewPolicyFindings,
} from './domain';
export type {
  AutonomyMode,
  EnvironmentHealth,
  EnvironmentKind,
  EnvironmentSummary,
  FlockSummary,
  ReviewItem,
  ReviewKind,
  ReviewRiskClass,
  ReviewStatus,
  ReviewSummary,
  ValkyrieDashboard,
  ValkyrieResident,
  WakefulnessState,
} from './domain';
export type {
  AutonomyUpdateRequest,
  IOdinReviewService,
  IValkyrieService,
  ReviewDecisionRequest,
  ReviewListFilters,
} from './ports';
