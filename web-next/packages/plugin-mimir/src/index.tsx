import { createRoute, redirect } from '@tanstack/react-router';
import { Brain } from 'lucide-react';
import { definePlugin } from '@niuulabs/plugin-sdk';
import type { PluginCtx } from '@niuulabs/plugin-sdk';
import { MemoryExploreView } from './ui/memory/MemoryExploreView';
import { MemoryPagePage } from './ui/memory/MemoryPagePage';
import { RegistryWorkspace } from './ui/RegistryWorkspace';
import { MimirSubnav } from './ui/MimirSubnav';
import { MimirTopbar } from './ui/MimirTopbar';
import { validateMemoryViewSearch } from './application/memoryViewSearch';

/** A legacy route's `search.mount`, if any — everything else is dropped. */
function legacySearch(search: unknown): { mount?: string } {
  const record = search as Record<string, unknown>;
  return typeof record.mount === 'string' ? { mount: record.mount } : {};
}

/** Redirect a retired `/mimir/*` route to the Memory scene, keeping `mount`. */
function redirectToMemory({ location }: { location: { search: unknown } }): never {
  throw redirect({ to: '/mimir', search: legacySearch(location.search) as never });
}

export const mimirPlugin = definePlugin({
  id: 'mimir',
  rune: 'M',
  title: 'Mímir',
  subtitle: 'the well of knowledge',
  // Memory: what the platform and its residents know. The Memory scene
  // (Explore/Focus/Ask/Replay) is the home for both Simple and Advanced mode.
  simple: {
    tabs: ['memory'],
    title: 'Memory',
    subtitle: 'what niuu knows, and how sure it is',
    icon: <Brain size={17} aria-hidden="true" />,
  },
  tabs: [
    { id: 'memory', label: 'Memory', rune: '◎', path: '/mimir' },
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
      // Explore/Focus/Ask/Replay all deep-link through these, in both modes.
      validateSearch: validateMemoryViewSearch,
      component: MemoryExploreView,
    }),
    // Reading a page works in either mode: a link from a realm, a session, or
    // the scene's Focus panel lands on the same screen.
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/read',
      component: MemoryPagePage,
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
    // Asking a question is now answered inline in the Memory scene — carry
    // both q and mount across, everything else the flat page used stays put.
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/ask',
      beforeLoad: ({ location }) => {
        const search = location.search as Record<string, unknown>;
        throw redirect({
          to: '/mimir',
          search: { ...legacySearch(search), q: search.q } as never,
        });
      },
    }),
    // Legacy tabs — Pages/Sources/Search/Graph/Ingest/Entities — are all the
    // one Memory scene now; only the mount scope still carries over.
    ...['pages', 'sources', 'search', 'graph', 'ingest', 'entities'].map((legacy) =>
      createRoute({
        getParentRoute: () => rootRoute,
        path: `/mimir/${legacy}`,
        beforeLoad: redirectToMemory,
      }),
    ),
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
export type { WikilinkTarget } from './domain';
export { resolveWikilink, detectBrokenWikilinks } from './domain';

// UI components (plugin-local; promote to @niuulabs/ui when a second plugin needs them)
export { RegistryMountEditor, type RegistryMountEditorProps } from './ui/RegistryPage';
export type { KnowledgeDeployment, DeploymentStatus } from './domain/instances';
export { WikilinkPill } from './ui/components/WikilinkPill';
export { PageTypeGlyph } from './ui/components/PageTypeGlyph';
export { MountChip } from './ui/components/MountChip';
export { MemoryExploreView } from './ui/memory/MemoryExploreView';
export { MemoryPagePage } from './ui/memory/MemoryPagePage';
export { ProofPill } from './ui/memory/ProofPill';
export type { FactEvidence, EvidenceTrend, RelatedPage, ReviseRequest } from './domain/evidence';
