import { createRoute } from '@tanstack/react-router';
import { definePlugin } from '@niuulabs/plugin-sdk';
import type { PluginCtx } from '@niuulabs/plugin-sdk';
import { MimirPage } from './ui/MimirPage';
import { SearchPage } from './ui/SearchPage';
import { GraphPage } from './ui/GraphPage';
import { RavnsPage } from './ui/RavnsPage';
import { HealthPage } from './ui/HealthPage';
import { RegistryPage } from './ui/RegistryPage';
import { AnalyticsPage } from './ui/AnalyticsPage';
import { MimirSubnav } from './ui/MimirSubnav';
import { MimirTopbar } from './ui/MimirTopbar';

export const mimirPlugin = definePlugin({
  id: 'mimir',
  rune: 'ᛗ',
  title: 'Mímir',
  subtitle: 'the well of knowledge',
  tabs: [
    { id: 'overview', label: 'Overview', rune: '◎', path: '/mimir' },
    { id: 'pages', label: 'Pages', rune: '▤', path: '/mimir/pages' },
    { id: 'sources', label: 'Sources', rune: '↧', path: '/mimir/sources' },
    { id: 'search', label: 'Search', rune: '⌕', path: '/mimir/search' },
    { id: 'graph', label: 'Graph', rune: '⌖', path: '/mimir/graph' },
    { id: 'registry', label: 'Registry', rune: '⛁', path: '/mimir/registry' },
    { id: 'wardens', label: 'Wardens', rune: 'ᚢ', path: '/mimir/ravns' },
    { id: 'health', label: 'Health', rune: '✚', path: '/mimir/health' },
    { id: 'analytics', label: 'Analytics', rune: '∑', path: '/mimir/analytics' },
  ],
  routes: (rootRoute) => [
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir',
      component: MimirPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/pages',
      component: () => <MimirPage defaultTab="pages" />,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/sources',
      component: () => <MimirPage defaultTab="sources" />,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/search',
      component: SearchPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/graph',
      component: GraphPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/registry',
      component: RegistryPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/ravns',
      component: RavnsPage,
    }),
    // Legacy deep links: /ingest -> Sources (which owns the working ingest
    // form), /lint and /doctor -> the consolidated Health page, /dreams ->
    // Analytics (dream history lives in its telemetry section).
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/ingest',
      component: () => <MimirPage defaultTab="sources" />,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/health',
      component: HealthPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/lint',
      component: HealthPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/doctor',
      component: HealthPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/dreams',
      component: AnalyticsPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/analytics',
      component: AnalyticsPage,
    }),
  ],
  subnav: (ctx: PluginCtx) => <MimirSubnav ctx={ctx} />,
  topbarRight: (ctx: PluginCtx) => <MimirTopbar ctx={ctx} />,
});

export { createMimirMockAdapter } from './adapters/mock';
export { buildMimirHttpAdapter } from './adapters/http';
export type {
  IMimirService,
  IMountAdapter,
  IPageStore,
  IEmbeddingStore,
  ILintEngine,
  SearchMode,
  EmbeddingSearchResult,
  RecentWrite,
} from './ports';
export type {
  PageType,
  Confidence,
  Zone,
  ZoneKind,
  ZoneKeyFacts,
  ZoneRelationships,
  ZoneAssessment,
  ZoneTimeline,
  PageMeta,
  Page,
  SearchResult,
} from './domain/page';
export type { LintRule, IssueSeverity, LintIssue, LintReport, DreamCycle } from './domain/lint';
export type { WriteRoutingRule, RouteTestResult } from './domain/routing';
export { resolveRoute } from './domain/routing';
export type { RavnState, RavnBinding } from './domain/ravn-binding';
export type { Source, OriginType } from './domain/source';
export type { EntityKind, EntityMeta } from './domain/entity';
export type { RegistryMount } from './domain/registry';
export type { EvalMetrics, EvalReport, QueryLogEntry, QueryStats } from './domain/analytics';
export { zeroResultQueries } from './domain/analytics';
export type { DoctorStatus, DoctorCheck, DoctorReport } from './domain/doctor';
export { fixableChecks } from './domain/doctor';
export type {
  FileTreeDir,
  FileTreeLeaf,
  FileTreeItem,
  WikilinkTarget,
  ZoneEditState,
  ZoneEditAction,
} from './domain';
export {
  buildFileTree,
  mergeFileTrees,
  resolveWikilink,
  detectBrokenWikilinks,
  zoneEditReducer,
} from './domain';

// UI components (plugin-local; promote to @niuulabs/ui when a second plugin needs them)
export { WikilinkPill } from './ui/components/WikilinkPill';
export { PageTypeGlyph } from './ui/components/PageTypeGlyph';
export { MountChip } from './ui/components/MountChip';
export { OverviewView } from './ui/OverviewView';
export { PagesView } from './ui/PagesView';
export { SourcesView } from './ui/SourcesView';
