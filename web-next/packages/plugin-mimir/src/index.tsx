import { createRoute } from '@tanstack/react-router';
import { definePlugin } from '@niuulabs/plugin-sdk';
import type { PluginCtx } from '@niuulabs/plugin-sdk';
import { MimirPage } from './ui/MimirPage';
import { SearchPage } from './ui/SearchPage';
import { GraphPage } from './ui/GraphPage';
import { EntitiesPage } from './ui/EntitiesPage';
import { RavnsPage } from './ui/RavnsPage';
import { IngestPage } from './ui/IngestPage';
import { LintPage } from './ui/LintPage';
import { DreamsPage } from './ui/DreamsPage';
import { RegistryPage } from './ui/RegistryPage';
import { MimirSubnav } from './ui/MimirSubnav';
import { MimirTopbar } from './ui/MimirTopbar';

export const mimirPlugin = definePlugin({
  id: 'mimir',
  rune: 'ᛗ',
  title: 'Mímir',
  subtitle: 'the well of knowledge',
  tabs: [
    { id: 'overview', label: 'Overview', rune: '◎', path: '/mimir' },
    { id: 'pages', label: 'Pages', rune: '❑', path: '/mimir/pages' },
    { id: 'search', label: 'Search', rune: '⌕', path: '/mimir/search' },
    { id: 'graph', label: 'Graph', rune: '⌖', path: '/mimir/graph' },
    { id: 'registry', label: 'Registry', rune: '⛁', path: '/mimir/registry' },
    { id: 'wardens', label: 'Wardens', rune: 'ᚢ', path: '/mimir/ravns' },
    { id: 'ingest', label: 'Ingest', rune: '↧', path: '/mimir/ingest' },
    { id: 'lint', label: 'Lint', rune: '⚠', path: '/mimir/lint' },
    { id: 'dreams', label: 'Dreams', rune: '≡', path: '/mimir/dreams' },
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
      path: '/mimir/entities',
      component: EntitiesPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/ravns',
      component: RavnsPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/ingest',
      component: IngestPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/lint',
      component: LintPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/dreams',
      component: DreamsPage,
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
