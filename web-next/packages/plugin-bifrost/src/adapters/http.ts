import type { ApiClient } from '@niuulabs/query';
import type {
  BifrostAlias,
  BifrostCacheStats,
  BifrostModel,
  BifrostProvider,
  BifrostUsageResponse,
  BifrostUsageSummary,
  BifrostUsageBucket,
  IBifrostService,
} from '../ports';

interface RawModel {
  id: string;
  name: string;
  vendor?: string;
  provider?: string;
  tier?: string;
  color?: string;
  description?: string;
  cost_per_million_tokens?: number | null;
  vram_required?: string | null;
  session_definition?: string | null;
  supports_tools?: boolean;
  supports_thinking?: boolean;
  enabled?: boolean;
  aliases?: string[];
  provider_keys?: string[];
}

interface RawAlias {
  alias: string;
  target: string;
}

interface RawProvider {
  key: string;
  vendor: string;
  base_url: string;
  model_ids: string[];
  timeout_seconds: number;
  cost_per_token: number;
  state: string;
  detail: string;
}

interface RawUsageBucket {
  requests?: number;
  input_tokens?: number;
  output_tokens?: number;
  cache_read_tokens?: number;
  cache_write_tokens?: number;
  reasoning_tokens?: number;
  cost_usd?: number;
}

interface RawUsageRecord {
  request_id: string;
  agent_id: string;
  tenant_id: string;
  session_id: string;
  saga_id: string;
  model: string;
  provider: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  reasoning_tokens: number;
  cost_usd: number;
  latency_ms: number;
  streaming: boolean;
  cache_hit?: boolean;
  timestamp: string;
}

interface RawUsageResponse {
  summary: {
    total_requests?: number;
    total_input_tokens?: number;
    total_output_tokens?: number;
    total_cache_read_tokens?: number;
    total_cache_write_tokens?: number;
    total_reasoning_tokens?: number;
    total_cost_usd?: number;
    by_model?: Record<string, RawUsageBucket>;
    by_provider?: Record<string, RawUsageBucket>;
  };
  records: RawUsageRecord[];
}

interface RawCacheStats {
  hits?: number;
  misses?: number;
  hit_rate?: number;
  saved_tokens?: number;
  saved_input_tokens?: number;
  saved_output_tokens?: number;
  entries?: number;
}

function toUsageBucket(raw?: RawUsageBucket): BifrostUsageBucket {
  return {
    requests: raw?.requests ?? 0,
    inputTokens: raw?.input_tokens ?? 0,
    outputTokens: raw?.output_tokens ?? 0,
    cacheReadTokens: raw?.cache_read_tokens ?? 0,
    cacheWriteTokens: raw?.cache_write_tokens ?? 0,
    reasoningTokens: raw?.reasoning_tokens ?? 0,
    costUsd: raw?.cost_usd ?? 0,
  };
}

function toUsageSummary(raw: RawUsageResponse['summary']): BifrostUsageSummary {
  const byModel = Object.fromEntries(
    Object.entries(raw.by_model ?? {}).map(([key, value]) => [key, toUsageBucket(value)]),
  );
  const byProvider = Object.fromEntries(
    Object.entries(raw.by_provider ?? {}).map(([key, value]) => [key, toUsageBucket(value)]),
  );
  return {
    totalRequests: raw.total_requests ?? 0,
    totalInputTokens: raw.total_input_tokens ?? 0,
    totalOutputTokens: raw.total_output_tokens ?? 0,
    totalCacheReadTokens: raw.total_cache_read_tokens ?? 0,
    totalCacheWriteTokens: raw.total_cache_write_tokens ?? 0,
    totalReasoningTokens: raw.total_reasoning_tokens ?? 0,
    totalCostUsd: raw.total_cost_usd ?? 0,
    byModel,
    byProvider,
  };
}

function toModel(raw: RawModel): BifrostModel {
  return {
    id: raw.id,
    name: raw.name,
    vendor: raw.vendor,
    provider: raw.provider === 'local' ? 'local' : 'cloud',
    tier:
      raw.tier === 'frontier' ||
      raw.tier === 'execution' ||
      raw.tier === 'reasoning' ||
      raw.tier === 'balanced'
        ? raw.tier
        : 'balanced',
    color: raw.color ?? '#2563EB',
    description: raw.description ?? '',
    cost: raw.cost_per_million_tokens ?? undefined,
    vram: raw.vram_required ?? undefined,
    sessionDefinition: raw.session_definition ?? undefined,
    supportsTools: raw.supports_tools ?? true,
    supportsThinking: raw.supports_thinking ?? true,
    enabled: raw.enabled ?? true,
    aliases: raw.aliases ?? [],
    providerKeys: raw.provider_keys ?? [],
  };
}

function toProvider(raw: RawProvider): BifrostProvider {
  return {
    key: raw.key,
    vendor: raw.vendor,
    baseUrl: raw.base_url,
    modelIds: raw.model_ids ?? [],
    timeoutSeconds: raw.timeout_seconds ?? 0,
    costPerToken: raw.cost_per_token ?? 0,
    state:
      raw.state === 'healthy' ||
      raw.state === 'degraded' ||
      raw.state === 'unreachable' ||
      raw.state === 'unknown'
        ? raw.state
        : 'unknown',
    detail: raw.detail ?? '',
  };
}

function toUsage(raw: RawUsageResponse): BifrostUsageResponse {
  return {
    summary: toUsageSummary(raw.summary),
    records: (raw.records ?? []).map((record) => ({
      requestId: record.request_id,
      agentId: record.agent_id,
      tenantId: record.tenant_id,
      sessionId: record.session_id,
      sagaId: record.saga_id,
      model: record.model,
      provider: record.provider,
      inputTokens: record.input_tokens,
      outputTokens: record.output_tokens,
      cacheReadTokens: record.cache_read_tokens,
      cacheWriteTokens: record.cache_write_tokens,
      reasoningTokens: record.reasoning_tokens,
      costUsd: record.cost_usd,
      latencyMs: record.latency_ms,
      streaming: record.streaming,
      cacheHit: record.cache_hit ?? false,
      timestamp: record.timestamp,
    })),
  };
}

function toCacheStats(raw: RawCacheStats): BifrostCacheStats {
  return {
    hits: raw.hits ?? 0,
    misses: raw.misses ?? 0,
    hitRate: raw.hit_rate ?? 0,
    savedTokens: raw.saved_tokens ?? 0,
    savedInputTokens: raw.saved_input_tokens ?? 0,
    savedOutputTokens: raw.saved_output_tokens ?? 0,
    entries: raw.entries ?? 0,
  };
}

export function buildBifrostHttpAdapter(client: ApiClient): IBifrostService {
  return {
    async listModels() {
      const payload = await client.get<RawModel[]>('/models');
      return payload.map(toModel);
    },
    async getModelCatalog() {
      const models = await client.get<RawModel[]>('/models');
      return Object.fromEntries(models.map((model) => [model.id, toModel(model)]));
    },
    async listAliases() {
      const payload = await client.get<RawAlias[]>('/aliases');
      return payload.map((entry): BifrostAlias => ({ alias: entry.alias, target: entry.target }));
    },
    async listProviders() {
      const payload = await client.get<RawProvider[]>('/providers/health');
      return payload.map(toProvider);
    },
    async getUsage(limit = 100) {
      const payload = await client.get<RawUsageResponse>(`/v1/usage?limit=${limit}`);
      return toUsage(payload);
    },
    async getCacheStats() {
      const payload = await client.get<RawCacheStats>('/v1/cache/stats');
      return toCacheStats(payload);
    },
  };
}
