export type BifrostModelProvider = 'cloud' | 'local';
export type BifrostModelTier = 'frontier' | 'balanced' | 'execution' | 'reasoning';
export type BifrostProviderState = 'healthy' | 'degraded' | 'unreachable' | 'unknown';

export interface BifrostModel {
  id: string;
  name: string;
  vendor?: string;
  provider: BifrostModelProvider;
  tier: BifrostModelTier;
  color: string;
  description: string;
  cost?: number;
  vram?: string;
  sessionDefinition?: string;
  supportsTools: boolean;
  supportsThinking: boolean;
  enabled: boolean;
  aliases: string[];
  providerKeys: string[];
}

export interface BifrostAlias {
  alias: string;
  target: string;
}

export interface BifrostProvider {
  key: string;
  vendor: string;
  baseUrl: string;
  modelIds: string[];
  timeoutSeconds: number;
  costPerToken: number;
  state: BifrostProviderState;
  detail: string;
}

export interface BifrostUsageBucket {
  requests: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  reasoningTokens: number;
  costUsd: number;
}

export interface BifrostUsageRecord {
  requestId: string;
  agentId: string;
  tenantId: string;
  sessionId: string;
  sagaId: string;
  model: string;
  provider: string;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  reasoningTokens: number;
  costUsd: number;
  latencyMs: number;
  streaming: boolean;
  cacheHit: boolean;
  timestamp: string;
}

export interface BifrostUsageSummary {
  totalRequests: number;
  totalInputTokens: number;
  totalOutputTokens: number;
  totalCacheReadTokens: number;
  totalCacheWriteTokens: number;
  totalReasoningTokens: number;
  totalCostUsd: number;
  byModel: Record<string, BifrostUsageBucket>;
  byProvider: Record<string, BifrostUsageBucket>;
}

export interface BifrostUsageResponse {
  summary: BifrostUsageSummary;
  records: BifrostUsageRecord[];
}

export interface BifrostCacheStats {
  hits: number;
  misses: number;
  hitRate: number;
  savedTokens: number;
  savedInputTokens: number;
  savedOutputTokens: number;
  entries: number;
}

export interface IBifrostService {
  listModels(): Promise<BifrostModel[]>;
  getModelCatalog(): Promise<Record<string, BifrostModel>>;
  listAliases(): Promise<BifrostAlias[]>;
  listProviders(): Promise<BifrostProvider[]>;
  getUsage(limit?: number): Promise<BifrostUsageResponse>;
  getCacheStats(): Promise<BifrostCacheStats>;
}
