/**
 * HTTP adapter for the Mímir service.
 *
 * Accepts an ApiClient scoped to the Mímir base URL and implements
 * IMimirService by mapping HTTP responses to domain types.
 */

import type { ApiClient } from '@niuulabs/query';
import type { Mount } from '@niuulabs/domain';
import type { IMimirService, SearchMode, RecentWrite } from '../ports';
import type { PageMeta, Page, SearchResult } from '../domain/page';
import type { Source, OriginType } from '../domain/source';
import type {
  LintReport,
  DreamCycle,
  LintIssue,
  IssueSeverity,
  LintRule,
  ActivityEvent,
  ActivityEventKind,
} from '../domain/lint';
import type { MimirStats, MimirGraph, GraphNode, GraphEdge } from '../domain/api-types';
import type { EmbeddingSearchResult } from '../ports/IEmbeddingStore';
import type { EntityKind, EntityMeta } from '../domain/entity';
import type { WriteRoutingRule } from '../domain/routing';
import type { RavnBinding } from '../domain/ravn-binding';
import type { RegistryMount } from '../domain/registry';
import { tallySeverity } from '../domain/lint';

// ---------------------------------------------------------------------------
// Raw wire types
// ---------------------------------------------------------------------------

interface RawMount {
  name: string;
  role: string;
  host: string;
  url: string;
  priority: number;
  categories: string[] | null;
  status: string;
  pages: number;
  sources: number;
  lint_issues: number;
  last_write: string;
  embedding: string;
  size_kb: number;
  desc: string;
}

interface RawRegistryMount {
  id: string;
  name: string;
  kind: 'local' | 'remote';
  lifecycle: 'registered' | 'ephemeral';
  role: string;
  url: string;
  path: string;
  categories: string[] | null;
  auth_ref?: string | null;
  default_read_priority: number;
  enabled: boolean;
  health_status: 'healthy' | 'degraded' | 'down' | 'unknown';
  health_message: string;
  desc: string;
}

interface RawPageMeta {
  path: string;
  title: string;
  summary: string;
  category: string;
  type?: string;
  confidence?: string;
  entity_type?: string;
  mounts?: string[];
  updated_at: string;
  updated_by?: string;
  source_ids: string[];
  size?: number;
}

interface RawPage extends RawPageMeta {
  related: string[];
  zones?: Array<{
    kind: string;
    items?: unknown[];
    text?: string;
  }>;
}

interface RawStats {
  page_count: number;
  categories: string[];
  healthy: boolean;
}

interface RawSearchResult {
  path: string;
  title: string;
  summary: string;
  category: string;
  type?: string;
  confidence?: string;
}

interface RawLintIssue {
  id: string;
  severity: string;
  rule?: string;
  page?: string;
  page_path?: string;
  mount?: string;
  assignee?: string;
  auto_fix?: boolean;
  auto_fixable?: boolean;
  message: string;
}

interface RawLintReport {
  issues: RawLintIssue[];
  pages_checked: number;
}

interface RawEmbeddingResult {
  path: string;
  title: string;
  summary: string;
  score: number;
  mount_name: string;
}

interface RawRecentWrite {
  id: string;
  timestamp: string;
  mount: string;
  page: string;
  ravn: string;
  kind: string;
  message: string;
}

interface RawSource {
  id?: string;
  source_id?: string;
  title: string;
  origin_type?: string;
  source_type?: string;
  origin_url?: string;
  origin_path?: string;
  ingested_at: string;
  ingest_agent?: string;
  compiled_into?: string[];
  content?: string;
}

interface RawDreamCycle {
  id: string;
  timestamp: string;
  ravn: string;
  mounts: string[];
  pages_updated: number;
  entities_created: number;
  lint_fixes: number;
  duration_ms: number;
}

interface RawActivityEvent {
  id: string;
  timestamp: string;
  kind: string;
  mount: string;
  ravn: string;
  message: string;
  page?: string;
}

interface RawGraphNode {
  id: string;
  title: string;
  category: string;
  inbound_count?: number;
}

interface RawGraphEdge {
  source: string;
  target: string;
}

interface RawGraph {
  nodes: RawGraphNode[];
  edges: RawGraphEdge[];
}

interface RawEntityMeta {
  path: string;
  title: string;
  entity_kind: string;
  summary: string;
  relationship_count: number;
}

// ---------------------------------------------------------------------------
// Mapping helpers
// ---------------------------------------------------------------------------

function toMount(raw: RawMount): Mount {
  return {
    name: raw.name,
    role: raw.role as Mount['role'],
    host: raw.host,
    url: raw.url,
    priority: raw.priority,
    categories: raw.categories,
    status: raw.status as Mount['status'],
    pages: raw.pages,
    sources: raw.sources,
    lintIssues: raw.lint_issues,
    lastWrite: raw.last_write,
    embedding: raw.embedding,
    sizeKb: raw.size_kb,
    desc: raw.desc,
  };
}

function toRegistryMount(raw: RawRegistryMount): RegistryMount {
  return {
    id: raw.id,
    name: raw.name,
    kind: raw.kind,
    lifecycle: raw.lifecycle,
    role: raw.role as RegistryMount['role'],
    url: raw.url,
    path: raw.path,
    categories: raw.categories,
    authRef: raw.auth_ref,
    defaultReadPriority: raw.default_read_priority,
    enabled: raw.enabled,
    healthStatus: raw.health_status,
    healthMessage: raw.health_message,
    desc: raw.desc,
  };
}

function toPageMeta(raw: RawPageMeta): PageMeta {
  return {
    path: raw.path,
    title: raw.title,
    summary: raw.summary,
    category: raw.category,
    type: (raw.type ?? inferPageType(raw.path, raw.category)) as PageMeta['type'],
    confidence: (raw.confidence ?? 'medium') as PageMeta['confidence'],
    entityType: raw.entity_type,
    mounts: raw.mounts ?? ['local'],
    updatedAt: raw.updated_at,
    updatedBy: raw.updated_by ?? 'mimir',
    sourceIds: raw.source_ids,
    size: raw.size ?? 0,
  };
}

function toPage(raw: RawPage): Page {
  return {
    ...toPageMeta(raw),
    related: raw.related,
  };
}

function toLintIssue(raw: RawLintIssue): LintIssue {
  return {
    id: raw.id,
    rule: (raw.rule ?? raw.id) as LintRule,
    severity: normalizeSeverity(raw.severity),
    page: raw.page ?? raw.page_path ?? '',
    mount: raw.mount ?? 'local',
    assignee: raw.assignee,
    autoFix: raw.auto_fix ?? raw.auto_fixable ?? false,
    message: raw.message,
  };
}

function toLintReport(raw: RawLintReport): LintReport {
  const issues = raw.issues.map(toLintIssue);
  return {
    issues,
    pagesChecked: raw.pages_checked,
    summary: tallySeverity(issues),
  };
}

function toEmbeddingResult(raw: RawEmbeddingResult): EmbeddingSearchResult {
  return {
    path: raw.path,
    title: raw.title,
    summary: raw.summary,
    score: raw.score,
    mountName: raw.mount_name,
  };
}

function toRecentWrite(raw: RawRecentWrite): RecentWrite {
  return {
    id: raw.id,
    timestamp: raw.timestamp,
    mount: raw.mount,
    page: raw.page,
    ravn: raw.ravn,
    kind: raw.kind as RecentWrite['kind'],
    message: raw.message,
  };
}

function toSource(raw: RawSource): Source {
  return {
    id: raw.id ?? raw.source_id ?? raw.title,
    title: raw.title,
    originType: normalizeOriginType(raw.origin_type ?? raw.source_type),
    originUrl: raw.origin_url,
    originPath: raw.origin_path,
    ingestedAt: raw.ingested_at,
    ingestAgent: raw.ingest_agent ?? 'mimir',
    compiledInto: raw.compiled_into ?? [],
    content: raw.content ?? '',
  };
}

function toDreamCycle(raw: RawDreamCycle): DreamCycle {
  return {
    id: raw.id,
    timestamp: raw.timestamp,
    ravn: raw.ravn,
    mounts: raw.mounts,
    pagesUpdated: raw.pages_updated,
    entitiesCreated: raw.entities_created,
    lintFixes: raw.lint_fixes,
    durationMs: raw.duration_ms,
  };
}

function toActivityEvent(raw: RawActivityEvent): ActivityEvent {
  return {
    id: raw.id,
    timestamp: raw.timestamp,
    kind: raw.kind as ActivityEventKind,
    mount: raw.mount,
    ravn: raw.ravn,
    message: raw.message,
    page: raw.page,
  };
}

function toGraphNode(raw: RawGraphNode): GraphNode {
  return {
    id: raw.id,
    title: raw.title,
    category: raw.category,
    inboundCount: raw.inbound_count,
  };
}

function toGraphEdge(raw: RawGraphEdge): GraphEdge {
  return { source: raw.source, target: raw.target };
}

function toGraph(raw: RawGraph): MimirGraph {
  return {
    nodes: raw.nodes.map(toGraphNode),
    edges: raw.edges.map(toGraphEdge),
  };
}

function toEntityMeta(raw: RawEntityMeta): EntityMeta {
  return {
    path: raw.path,
    title: raw.title,
    entityKind: raw.entity_kind as EntityKind,
    summary: raw.summary,
    relationshipCount: raw.relationship_count,
  };
}

function isMissingRouteError(error: unknown): error is { status: number } {
  return (
    typeof error === 'object' &&
    error !== null &&
    'status' in error &&
    typeof (error as { status?: unknown }).status === 'number' &&
    (((error as { status: number }).status >= 404 && (error as { status: number }).status < 406) ||
      (error as { status: number }).status === 501)
  );
}

function inferPageType(path: string, category: string): PageMeta['type'] {
  if (path.startsWith('/entities/') || category === 'entity') return 'entity';
  if (path.includes('/decisions/') || category === 'decision') return 'decision';
  if (path.includes('/preferences/') || category === 'preference') return 'preference';
  if (path.includes('/directives/') || category === 'directive') return 'directive';
  return 'topic';
}

function normalizeSeverity(severity: string): IssueSeverity {
  if (severity === 'warning') return 'warn';
  return severity as IssueSeverity;
}

function normalizeOriginType(originType: string | undefined): OriginType {
  switch (originType) {
    case 'web':
    case 'rss':
    case 'arxiv':
    case 'file':
    case 'mail':
    case 'chat':
      return originType;
    case 'document':
      return 'file';
    case 'conversation':
      return 'chat';
    default:
      return 'file';
  }
}

function inferEntityKind(path: string, title: string, summary: string): EntityKind {
  const haystack = `${path} ${title} ${summary}`.toLowerCase();
  if (haystack.includes('/people/') || haystack.includes(' person ')) return 'person';
  if (
    haystack.includes('/org') ||
    haystack.includes(' organization') ||
    haystack.includes(' organisation')
  ) {
    return 'org';
  }
  if (haystack.includes('/project') || haystack.includes(' project ')) return 'project';
  if (haystack.includes('/component') || haystack.includes(' component ')) return 'component';
  if (haystack.includes('/tech') || haystack.includes(' technology ')) return 'technology';
  return 'concept';
}

async function listLegacySources(client: ApiClient): Promise<Source[]> {
  const raw = await client.get<RawSource[]>('/sources');
  return raw.map(toSource);
}

// ---------------------------------------------------------------------------
// Adapter factory
// ---------------------------------------------------------------------------

export function buildMimirHttpAdapter(client: ApiClient): IMimirService {
  return {
    mounts: {
      async listMounts(): Promise<Mount[]> {
        try {
          const raw = await client.get<RawMount[]>('/mounts');
          return raw.map(toMount);
        } catch (error) {
          if (!isMissingRouteError(error)) throw error;
          const stats = await client.get<RawStats>('/stats');
          return [
            {
              name: 'local',
              role: 'local',
              host: 'embedded',
              url: '',
              priority: 1,
              categories: stats.categories,
              status: stats.healthy ? 'healthy' : 'degraded',
              pages: stats.page_count,
              sources: 0,
              lintIssues: 0,
              lastWrite: '',
              embedding: 'fts',
              sizeKb: 0,
              desc: 'Current Mimir instance',
            },
          ];
        }
      },

      async listRegistryMounts(): Promise<RegistryMount[]> {
        try {
          const raw = await client.get<RawRegistryMount[]>('/registry/mounts');
          return raw.map(toRegistryMount);
        } catch (error) {
          if (!isMissingRouteError(error)) throw error;
          const mounts = await this.listMounts();
          return mounts.map((mount) => ({
            id: `registry-${mount.name}`,
            name: mount.name,
            kind: mount.url ? 'remote' : 'local',
            lifecycle: 'registered',
            role: mount.role,
            url: mount.url,
            path: '',
            categories: mount.categories,
            authRef: null,
            defaultReadPriority: mount.priority,
            enabled: mount.status !== 'down',
            healthStatus: mount.status === 'healthy' ? 'healthy' : mount.status,
            healthMessage: '',
            desc: mount.desc,
          }));
        }
      },

      async createRegistryMount(mount: Omit<RegistryMount, 'id'>): Promise<RegistryMount> {
        const raw = await client.post<RawRegistryMount>('/registry/mounts', {
          name: mount.name,
          kind: mount.kind,
          lifecycle: mount.lifecycle,
          role: mount.role,
          url: mount.url,
          path: mount.path,
          categories: mount.categories,
          auth_ref: mount.authRef ?? null,
          default_read_priority: mount.defaultReadPriority,
          enabled: mount.enabled,
          health_status: mount.healthStatus,
          health_message: mount.healthMessage,
          desc: mount.desc,
        });
        return toRegistryMount(raw);
      },

      async updateRegistryMount(
        id: string,
        mount: Omit<RegistryMount, 'id'>,
      ): Promise<RegistryMount> {
        const raw = await client.put<RawRegistryMount>(`/registry/mounts/${id}`, {
          name: mount.name,
          kind: mount.kind,
          lifecycle: mount.lifecycle,
          role: mount.role,
          url: mount.url,
          path: mount.path,
          categories: mount.categories,
          auth_ref: mount.authRef ?? null,
          default_read_priority: mount.defaultReadPriority,
          enabled: mount.enabled,
          health_status: mount.healthStatus,
          health_message: mount.healthMessage,
          desc: mount.desc,
        });
        return toRegistryMount(raw);
      },

      async deleteRegistryMount(id: string): Promise<void> {
        await client.delete<void>(`/registry/mounts/${id}`);
      },

      async listRoutingRules(): Promise<WriteRoutingRule[]> {
        try {
          return await client.get<WriteRoutingRule[]>('/routing/rules');
        } catch (error) {
          if (!isMissingRouteError(error)) throw error;
          return [];
        }
      },

      async upsertRoutingRule(rule: WriteRoutingRule): Promise<WriteRoutingRule> {
        return client.put<WriteRoutingRule>(`/routing/rules/${rule.id}`, rule);
      },

      async deleteRoutingRule(id: string): Promise<void> {
        await client.delete<void>(`/routing/rules/${id}`);
      },

      async listRavnBindings(): Promise<RavnBinding[]> {
        try {
          return await client.get<RavnBinding[]>('/ravns/bindings');
        } catch (error) {
          if (!isMissingRouteError(error)) throw error;
          return [];
        }
      },

      async getRecentWrites(limit?: number): Promise<RecentWrite[]> {
        const qs = limit != null ? `?limit=${limit}` : '';
        try {
          const raw = await client.get<RawRecentWrite[]>(`/mounts/recent-writes${qs}`);
          return raw.map(toRecentWrite);
        } catch (error) {
          if (!isMissingRouteError(error)) throw error;
          const sources = await listLegacySources(client);
          return [...sources]
            .sort((a, b) => b.ingestedAt.localeCompare(a.ingestedAt))
            .slice(0, limit ?? sources.length)
            .map((source) => ({
              id: source.id,
              timestamp: source.ingestedAt,
              mount: 'local',
              page: source.compiledInto[0] ?? '',
              ravn: source.ingestAgent,
              kind: 'compile',
              message: source.title,
            }));
        }
      },
    },

    pages: {
      async getStats(): Promise<MimirStats> {
        const raw = await client.get<RawStats>('/stats');
        return {
          pageCount: raw.page_count,
          categories: raw.categories,
          healthy: raw.healthy,
        };
      },

      async listPages(options): Promise<PageMeta[]> {
        const params = new URLSearchParams();
        if (options?.mountName) params.set('mount', options.mountName);
        if (options?.category) params.set('category', options.category);
        const qs = params.toString() ? `?${params.toString()}` : '';
        const raw = await client.get<RawPageMeta[]>(`/pages${qs}`);
        return raw.map(toPageMeta);
      },

      async getPage(path: string, mountName?: string): Promise<Page | null> {
        const params = new URLSearchParams({ path });
        if (mountName) params.set('mount', mountName);
        try {
          const raw = await client.get<RawPage | null>(`/page?${params.toString()}`);
          if (!raw) return null;
          return toPage(raw);
        } catch (error) {
          if (!isMissingRouteError(error)) throw error;
          return null;
        }
      },

      async upsertPage(path: string, content: string, mountName?: string): Promise<void> {
        const body: Record<string, string> = { path, content };
        if (mountName) body['mount'] = mountName;
        await client.put<void>('/page', body);
      },

      async search(query: string, mode: SearchMode = 'hybrid'): Promise<SearchResult[]> {
        const raw = await client.get<RawSearchResult[]>(
          `/search?q=${encodeURIComponent(query)}&mode=${mode}`,
        );
        return raw.map((r) => ({
          path: r.path,
          title: r.title,
          summary: r.summary,
          category: r.category,
          type: (r.type ?? inferPageType(r.path, r.category)) as SearchResult['type'],
          confidence: (r.confidence ?? 'medium') as SearchResult['confidence'],
        }));
      },

      async getGraph(options): Promise<MimirGraph> {
        const qs = options?.mountName ? `?mount=${encodeURIComponent(options.mountName)}` : '';
        const raw = await client.get<RawGraph>(`/graph${qs}`);
        return toGraph(raw);
      },

      async listEntities(options): Promise<EntityMeta[]> {
        const qs = options?.kind ? `?kind=${encodeURIComponent(options.kind)}` : '';
        try {
          const raw = await client.get<RawEntityMeta[]>(`/entities${qs}`);
          return raw.map(toEntityMeta);
        } catch (error) {
          if (!isMissingRouteError(error)) throw error;
          const pages = await client.get<RawPageMeta[]>('/pages');
          return pages
            .filter((page) => page.path.startsWith('/entities/') || page.category === 'entity')
            .map((page) => ({
              path: page.path,
              title: page.title,
              entityKind: inferEntityKind(page.path, page.title, page.summary),
              summary: page.summary,
              relationshipCount: 0,
            }))
            .filter((entity) => (options?.kind ? entity.entityKind === options.kind : true));
        }
      },

      async listSources(options?: {
        originType?: OriginType;
        mountName?: string;
      }): Promise<Source[]> {
        const params = new URLSearchParams();
        if (options?.originType) params.set('origin_type', options.originType);
        if (options?.mountName) params.set('mount', options.mountName);
        const qs = params.toString() ? `?${params.toString()}` : '';
        try {
          const raw = await client.get<RawSource[]>(`/sources${qs}`);
          return raw.map(toSource);
        } catch (error) {
          if (!isMissingRouteError(error)) throw error;
          const sources = await listLegacySources(client);
          return sources.filter((source) =>
            options?.originType ? source.originType === options.originType : true,
          );
        }
      },

      async getPageSources(path: string): Promise<Source[]> {
        try {
          const raw = await client.get<RawSource[]>(
            `/page/sources?path=${encodeURIComponent(path)}`,
          );
          return raw.map(toSource);
        } catch (error) {
          if (!isMissingRouteError(error)) throw error;
          const page = await this.getPage(path);
          if (!page || page.sourceIds.length === 0) return [];
          const sources = await Promise.all(
            page.sourceIds.map((sourceId) =>
              client.get<RawSource>(`/source?source_id=${encodeURIComponent(sourceId)}`),
            ),
          );
          return sources.map(toSource);
        }
      },

      async ingestUrl(url: string): Promise<Source> {
        try {
          const raw = await client.post<RawSource>('/sources/ingest/url', { url });
          return toSource(raw);
        } catch (error) {
          if (!isMissingRouteError(error)) throw error;
          throw new Error('URL ingest is not supported by the current Mimir backend');
        }
      },

      async ingestFile(file: File): Promise<Source> {
        const form = new FormData();
        form.append('file', file);
        try {
          const raw = await client.post<RawSource>('/sources/ingest/file', form);
          return toSource(raw);
        } catch (error) {
          if (!isMissingRouteError(error)) throw error;
          const fileContent =
            typeof file.text === 'function'
              ? await file.text()
              : new TextDecoder().decode(await file.arrayBuffer());
          const raw = await client.post<{ source_id: string; pages_updated: string[] }>('/ingest', {
            title: file.name,
            content: fileContent,
            source_type: 'document',
          });
          return {
            id: raw.source_id,
            title: file.name,
            originType: 'file',
            originPath: file.name,
            ingestedAt: new Date().toISOString(),
            ingestAgent: 'mimir',
            compiledInto: raw.pages_updated,
            content: '',
          };
        }
      },
    },

    embeddings: {
      async semanticSearch(
        query: string,
        topK = 10,
        mountName?: string,
      ): Promise<EmbeddingSearchResult[]> {
        const params = new URLSearchParams({ q: query, top_k: String(topK) });
        if (mountName) params.set('mount', mountName);
        try {
          const raw = await client.get<RawEmbeddingResult[]>(
            `/embeddings/search?${params.toString()}`,
          );
          return raw.map(toEmbeddingResult);
        } catch (error) {
          if (!isMissingRouteError(error)) throw error;
          const results = await client.get<RawSearchResult[]>(
            `/search?q=${encodeURIComponent(query)}&mode=fts`,
          );
          return results.slice(0, topK).map((result, index) => ({
            path: result.path,
            title: result.title,
            summary: result.summary,
            score: Math.max(0, 1 - index * 0.1),
            mountName: mountName ?? 'local',
          }));
        }
      },
    },

    lint: {
      async getLintReport(mountName?: string): Promise<LintReport> {
        const qs = mountName ? `?mount=${encodeURIComponent(mountName)}` : '';
        const raw = await client.get<RawLintReport>(`/lint${qs}`);
        return toLintReport(raw);
      },

      async runAutoFix(issueIds?: string[]): Promise<LintReport> {
        const body = issueIds ? { issue_ids: issueIds } : {};
        const raw = await client.post<RawLintReport>('/lint/fix', body);
        return toLintReport(raw);
      },

      async getDreamCycles(limit = 20): Promise<DreamCycle[]> {
        try {
          const raw = await client.get<RawDreamCycle[]>(`/dreams?limit=${limit}`);
          return raw.map(toDreamCycle);
        } catch (error) {
          if (!isMissingRouteError(error)) throw error;
          return [];
        }
      },

      async getActivityLog(limit = 50): Promise<ActivityEvent[]> {
        try {
          const raw = await client.get<RawActivityEvent[]>(`/activity?limit=${limit}`);
          return raw.map(toActivityEvent);
        } catch (error) {
          if (!isMissingRouteError(error)) throw error;
          return [];
        }
      },

      async reassignIssues(issueIds: string[], assignee: string): Promise<LintReport> {
        const raw = await client.post<RawLintReport>('/lint/reassign', {
          issue_ids: issueIds,
          assignee,
        });
        return toLintReport(raw);
      },
    },
  };
}
