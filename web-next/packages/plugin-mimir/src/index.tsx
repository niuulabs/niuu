import { createRoute } from '@tanstack/react-router';
import { Brain } from 'lucide-react';
import { definePlugin } from '@niuulabs/plugin-sdk';
import type { PluginCtx } from '@niuulabs/plugin-sdk';
import { MimirPage } from './ui/MimirPage';
import { MemoryOverviewRoute } from './ui/memory/MemoryOverviewRoute';
import { AskMemoryPage } from './ui/memory/AskMemoryPage';
import { MemoryPagePage } from './ui/memory/MemoryPagePage';
import { SearchPage } from './ui/SearchPage';
import { GraphPage } from './ui/GraphPage';
import { RegistryWorkspace } from './ui/RegistryWorkspace';
import { MimirSubnav } from './ui/MimirSubnav';
import { MimirTopbar } from './ui/MimirTopbar';

export const mimirPlugin = definePlugin({
  id: 'mimir',
  rune: 'M',
  title: 'Mímir',
  subtitle: 'the well of knowledge',
  // Memory: what the platform and its residents know.
  simple: {
    tabs: ['overview', 'pages'],
    title: 'Memory',
    subtitle: 'what niuu knows, and how sure it is',
    icon: <Brain size={17} aria-hidden="true" />,
  },
  tabs: [
    { id: 'overview', label: 'Overview', rune: '◎', path: '/mimir' },
    { id: 'pages', label: 'Pages', rune: '▤', path: '/mimir/pages' },
    { id: 'sources', label: 'Sources', rune: '↧', path: '/mimir/sources' },
    { id: 'graph', label: 'Graph', rune: '⌖', path: '/mimir/graph' },
    { id: 'registry', label: 'Registry', rune: '⛁', path: '/mimir/registry' },
  ],
  routes: (rootRoute) => [
    ...['wardens', 'health', 'analytics'].map((section) =>
      createRoute({
        getParentRoute: () => rootRoute,
        path: `/mimir/registry/${section}`,
        component: RegistryWorkspace,
      }),
    ),

    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir',
      component: MemoryOverviewRoute,
    }),
    // Ask and read work in either mode: a link from a realm, a session, or the
    // Advanced pages view lands on the same screen.
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/ask',
      component: AskMemoryPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/read',
      component: MemoryPagePage,
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
      component: RegistryWorkspace,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/ravns',
      component: RegistryWorkspace,
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
      component: RegistryWorkspace,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/lint',
      component: RegistryWorkspace,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/doctor',
      component: RegistryWorkspace,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/dreams',
      component: RegistryWorkspace,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/analytics',
      component: RegistryWorkspace,
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
export { RegistryMountEditor, type RegistryMountEditorProps } from './ui/RegistryPage';
export type { KnowledgeDeployment, DeploymentStatus } from './domain/instances';
export { WikilinkPill } from './ui/components/WikilinkPill';
export { PageTypeGlyph } from './ui/components/PageTypeGlyph';
export { MountChip } from './ui/components/MountChip';
export { MemoryHomePage } from './ui/memory/MemoryHomePage';
export { AskMemoryPage } from './ui/memory/AskMemoryPage';
export { MemoryPagePage } from './ui/memory/MemoryPagePage';
export { ProofPill } from './ui/memory/ProofPill';
export type { FactEvidence, EvidenceTrend, RelatedPage, ReviseRequest } from './domain/evidence';
export { OverviewView } from './ui/OverviewView';
export { PagesView } from './ui/PagesView';
export { SourcesView } from './ui/SourcesView';
