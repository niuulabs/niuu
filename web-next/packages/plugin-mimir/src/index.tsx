import { createRoute, redirect } from '@tanstack/react-router';
import { MemoryIcon } from '@niuulabs/ui';
import { definePlugin } from '@niuulabs/plugin-sdk';
import type { PluginCtx } from '@niuulabs/plugin-sdk';
import { MemoryExploreView } from './ui/memory/MemoryExploreView';
import { MemoryPagePage } from './ui/memory/MemoryPagePage';
import { RegistryWorkspace } from './ui/RegistryWorkspace';
import { MimirSubnav } from './ui/MimirSubnav';
import { MimirTopbar } from './ui/MimirTopbar';
import { validateMemoryViewSearch } from './application/memoryViewSearch';
import { SearchPage } from './ui/everyday/SearchPage';
import { ReviewPage } from './ui/everyday/ReviewPage';

/** A legacy route's `search.mount`, if any — everything else is dropped. */
function legacySearch(search: unknown): { mount?: string } {
  const record = search as Record<string, unknown>;
  return typeof record.mount === 'string' ? { mount: record.mount } : {};
}

/** Redirect a retired `/mimir/*` route to the Memory scene, keeping `mount`. */
function redirectToMemory({ location }: { location: { search: unknown } }): never {
  throw redirect({ to: '/mimir', search: legacySearch(location.search) as never });
}

/** What the Search page reads from the address: one project to narrow to, and a query. */
export function validateSearchPageSearch(search: Record<string, unknown>): {
  project?: string;
  q?: string;
} {
  const out: { project?: string; q?: string } = {};
  if (search.project !== undefined && search.project !== '') out.project = String(search.project);
  if (search.q !== undefined && search.q !== '') out.q = String(search.q);
  return out;
}

/** Redirect the retired `/mimir/ask` to Search, keeping `project` and `q`. */
function redirectToSearch({ location }: { location: { search: unknown } }): never {
  const search = validateSearchPageSearch(location.search as Record<string, unknown>);
  throw redirect({ to: '/mimir/search', search: search as never });
}

export const mimirPlugin = definePlugin({
  id: 'mimir',
  rune: 'M',
  icon: <MemoryIcon />,
  title: 'Memory',
  subtitle: 'what is known',
  // Memory: what the platform and its residents know. The Memory scene
  // (Explore/Focus/Ask/Replay) is its home.
  tabs: [
    { id: 'memory', label: 'Explore', path: '/mimir' },
    { id: 'search', label: 'Search', path: '/mimir/search' },
    { id: 'review', label: 'Review', path: '/mimir/review' },
    { id: 'registry', label: 'Library', path: '/mimir/registry' },
  ],
  routes: (rootRoute) => [
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/registry/analytics',
      component: RegistryWorkspace,
    }),

    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir',
      // Explore/Focus/Ask/Replay all deep-link through these.
      validateSearch: validateMemoryViewSearch,
      component: MemoryExploreView,
    }),
    // A link from a realm, a session, or the scene's Focus panel lands on the
    // same screen.
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
      path: '/mimir/analytics',
      component: RegistryWorkspace,
    }),
    // Search what the team knows, and keep or discard what agents propose.
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/search',
      validateSearch: validateSearchPageSearch,
      component: SearchPage,
    }),
    // The page used to be called Ask; old links and bookmarks land on Search.
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/ask',
      beforeLoad: redirectToSearch,
    }),
    createRoute({ getParentRoute: () => rootRoute, path: '/mimir/review', component: ReviewPage }),
    // Legacy tabs — Pages/Sources/Search/Graph/Ingest/Entities, and the retired
    // Health/Lint/Doctor — are all the one Memory scene now; only the mount scope still carries over.
    ...['pages', 'sources', 'graph', 'ingest', 'entities', 'health', 'lint', 'doctor'].map(
      (legacy) =>
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
export {
  buildMemoryGatewayHttpAdapter,
  createMemoryGatewayMockAdapter,
} from './adapters/memoryGateway';
export {
  MEMORY_GATEWAY_SERVICE_KEY,
  type IMemoryGatewayService,
  type MemorySearchOptions,
} from './ports/IMemoryGateway';
export type {
  MemoryHit,
  MemoryProposal,
  MemoryReview,
  MemorySearchResult,
  ReviewSource,
} from './domain/memoryGateway';
export { SearchPage } from './ui/everyday/SearchPage';
export { ReviewPage } from './ui/everyday/ReviewPage';
export { buildMimirHttpAdapter } from './adapters/http';
export type {
  IMimirService,
  IMountAdapter,
  IPageStore,
  IEmbeddingStore,
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
export type { Source, OriginType } from './domain/source';
export type { EntityKind, EntityMeta } from './domain/entity';
export type { RegistryMount } from './domain/registry';
export type { EvalMetrics, EvalReport, QueryLogEntry, QueryStats } from './domain/analytics';
export { zeroResultQueries } from './domain/analytics';
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
