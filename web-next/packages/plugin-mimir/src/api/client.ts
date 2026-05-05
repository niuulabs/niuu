/**
 * Mimir API client.
 *
 * Function-based client that wraps the shared ApiClient for Mimir endpoints.
 * In production the Mimir service is exposed at /mimir on the same origin
 * (routed by Envoy).
 *
 * Copied from web/src/modules/mimir/api/client.ts — import updated to
 * @niuulabs/query, all mapping logic preserved.
 */

import { createApiClient } from '@niuulabs/query';
import type {
  LintIssueHttp,
  MimirStats,
  MimirPageMeta,
  MimirPage,
  MimirSearchResult,
  MimirLintReport,
  MimirLogEntry,
  MimirGraph,
  GraphNode,
  GraphEdge,
  IngestRequest,
  IngestResponse,
} from '../domain/api-types';

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

const api = createApiClient('/mimir');

interface RawPageMeta {
  path: string;
  title: string;
  summary: string;
  category: string;
  updated_at: string;
  source_ids?: string[];
}

interface RawPage extends RawPageMeta {
  content: string;
}

interface RawStats {
  page_count: number;
  categories: string[];
  healthy: boolean;
}

interface RawLintIssue {
  id: string;
  severity: string;
  message: string;
  page_path: string;
  auto_fixable: boolean;
}

interface RawLintReport {
  issues: RawLintIssue[];
  pages_checked: number;
  issues_found: boolean;
  summary: { error: number; warning: number; info: number };
}

interface RawLogEntry {
  raw: string;
  entries: string[];
}

interface RawGraphNode {
  id: string;
  title: string;
  category: string;
}

interface RawGraphEdge {
  source: string;
  target: string;
}

interface RawGraph {
  nodes: RawGraphNode[];
  edges: RawGraphEdge[];
}

interface RawIngestResponse {
  source_id: string;
  pages_updated: string[];
}

function toMeta(raw: RawPageMeta): MimirPageMeta {
  return {
    path: raw.path,
    title: raw.title,
    summary: raw.summary,
    category: raw.category,
    updatedAt: raw.updated_at,
    sourceIds: raw.source_ids ?? [],
  };
}

function toPage(raw: RawPage): MimirPage {
  return {
    ...toMeta(raw),
    content: raw.content,
  };
}

function toStats(raw: RawStats): MimirStats {
  return {
    pageCount: raw.page_count,
    categories: raw.categories,
    healthy: raw.healthy,
  };
}

function toLintIssue(raw: RawLintIssue): LintIssueHttp {
  return {
    id: raw.id,
    severity: raw.severity as LintIssueHttp['severity'],
    message: raw.message,
    pagePath: raw.page_path,
    autoFixable: raw.auto_fixable,
  };
}

function toLintReport(raw: RawLintReport): MimirLintReport {
  return {
    issues: raw.issues.map(toLintIssue),
    pagesChecked: raw.pages_checked,
    issuesFound: raw.issues_found,
    summary: raw.summary,
  };
}

function toLogEntry(raw: RawLogEntry): MimirLogEntry {
  return {
    raw: raw.raw,
    entries: raw.entries,
  };
}

function toSearchResult(raw: RawPageMeta): MimirSearchResult {
  return {
    path: raw.path,
    title: raw.title,
    summary: raw.summary,
    category: raw.category,
  };
}

function toGraphNode(raw: RawGraphNode): GraphNode {
  return {
    id: raw.id,
    title: raw.title,
    category: raw.category,
  };
}

function toGraphEdge(raw: RawGraphEdge): GraphEdge {
  return {
    source: raw.source,
    target: raw.target,
  };
}

function toGraph(raw: RawGraph): MimirGraph {
  return {
    nodes: raw.nodes.map(toGraphNode),
    edges: raw.edges.map(toGraphEdge),
  };
}

function toIngestResponse(raw: RawIngestResponse): IngestResponse {
  return {
    sourceId: raw.source_id,
    pagesUpdated: raw.pages_updated,
  };
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/** GET /stats */
export async function getStats(): Promise<MimirStats> {
  const raw = await api.get<RawStats>('/stats');
  return toStats(raw);
}

/** GET /pages (optionally filtered by category) */
export async function listPages(category?: string): Promise<MimirPageMeta[]> {
  const qs = category ? `?category=${encodeURIComponent(category)}` : '';
  const raw = await api.get<RawPageMeta[]>(`/pages${qs}`);
  return raw.map(toMeta);
}

/** GET /page?path= */
export async function getPage(path: string): Promise<MimirPage> {
  const raw = await api.get<RawPage>(`/page?path=${encodeURIComponent(path)}`);
  return toPage(raw);
}

/** GET /search?q= */
export async function search(query: string): Promise<MimirSearchResult[]> {
  const raw = await api.get<RawPageMeta[]>(`/search?q=${encodeURIComponent(query)}`);
  return raw.map(toSearchResult);
}

/** GET /log?n= */
export async function getLog(n = 50): Promise<MimirLogEntry> {
  const raw = await api.get<RawLogEntry>(`/log?n=${n}`);
  return toLogEntry(raw);
}

/** GET /lint */
export async function getLint(): Promise<MimirLintReport> {
  const raw = await api.get<RawLintReport>('/lint');
  return toLintReport(raw);
}

/** POST /lint/fix — run lint and apply auto-fixes */
export async function lintFix(): Promise<MimirLintReport> {
  const raw = await api.post<RawLintReport>('/lint/fix', {});
  return toLintReport(raw);
}

/** PUT /page */
export async function upsertPage(path: string, content: string): Promise<void> {
  await api.put<void>('/page', { path, content });
}

/** GET /graph */
export async function getGraph(): Promise<MimirGraph> {
  const raw = await api.get<RawGraph>('/graph');
  return toGraph(raw);
}

/** POST /ingest */
export async function ingest(request: IngestRequest): Promise<IngestResponse> {
  const raw = await api.post<RawIngestResponse>('/ingest', {
    title: request.title,
    content: request.content,
    source_type: request.sourceType,
    origin_url: request.originUrl,
  });
  return toIngestResponse(raw);
}
