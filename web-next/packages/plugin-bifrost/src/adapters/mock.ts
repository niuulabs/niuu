import type {
  BifrostAlias,
  BifrostCacheStats,
  BifrostModel,
  BifrostProvider,
  BifrostUsageResponse,
  IBifrostService,
} from '../ports';

const MOCK_MODELS: BifrostModel[] = [
  {
    id: 'claude-sonnet-4-6',
    name: 'Claude Sonnet 4.6',
    vendor: 'anthropic',
    provider: 'cloud',
    tier: 'balanced',
    color: '#2563EB',
    description: 'Balanced Anthropic model for general execution and review work.',
    cost: 3.0,
    sessionDefinition: 'skuldClaude',
    supportsTools: true,
    supportsThinking: true,
    enabled: true,
    aliases: ['balanced'],
    providerKeys: ['anthropic'],
  },
  {
    id: 'gpt-5.5',
    name: 'GPT-5.5',
    vendor: 'openai',
    provider: 'cloud',
    tier: 'frontier',
    color: '#10B981',
    description: 'Frontier OpenAI model for coding and council workflows.',
    cost: 10.0,
    sessionDefinition: 'skuldCodex',
    supportsTools: true,
    supportsThinking: true,
    enabled: true,
    aliases: ['frontier'],
    providerKeys: ['openai'],
  },
];

const MOCK_PROVIDERS: BifrostProvider[] = [
  {
    key: 'anthropic',
    vendor: 'anthropic',
    baseUrl: 'https://api.anthropic.com',
    modelIds: ['claude-sonnet-4-6'],
    timeoutSeconds: 120,
    costPerToken: 0,
    state: 'healthy',
    detail: '',
  },
  {
    key: 'openai',
    vendor: 'openai',
    baseUrl: 'https://api.openai.com',
    modelIds: ['gpt-5.5'],
    timeoutSeconds: 120,
    costPerToken: 0,
    state: 'healthy',
    detail: '',
  },
];

const MOCK_ALIASES: BifrostAlias[] = [
  { alias: 'balanced', target: 'claude-sonnet-4-6' },
  { alias: 'frontier', target: 'gpt-5.5' },
];

const MOCK_USAGE: BifrostUsageResponse = {
  summary: {
    totalRequests: 42,
    totalInputTokens: 120000,
    totalOutputTokens: 41000,
    totalCacheReadTokens: 8000,
    totalCacheWriteTokens: 4000,
    totalReasoningTokens: 9000,
    totalCostUsd: 12.34,
    byModel: {
      'claude-sonnet-4-6': {
        requests: 21,
        inputTokens: 54000,
        outputTokens: 17000,
        cacheReadTokens: 4000,
        cacheWriteTokens: 1000,
        reasoningTokens: 3000,
        costUsd: 4.2,
      },
      'gpt-5.5': {
        requests: 21,
        inputTokens: 66000,
        outputTokens: 24000,
        cacheReadTokens: 4000,
        cacheWriteTokens: 3000,
        reasoningTokens: 6000,
        costUsd: 8.14,
      },
    },
    byProvider: {
      anthropic: {
        requests: 21,
        inputTokens: 54000,
        outputTokens: 17000,
        cacheReadTokens: 4000,
        cacheWriteTokens: 1000,
        reasoningTokens: 3000,
        costUsd: 4.2,
      },
      openai: {
        requests: 21,
        inputTokens: 66000,
        outputTokens: 24000,
        cacheReadTokens: 4000,
        cacheWriteTokens: 3000,
        reasoningTokens: 6000,
        costUsd: 8.14,
      },
    },
  },
  records: [
    {
      requestId: 'req-1',
      agentId: 'warden-test',
      tenantId: 'dev',
      sessionId: 'session-1',
      sagaId: 'saga-1',
      model: 'gpt-5.5',
      provider: 'openai',
      inputTokens: 3200,
      outputTokens: 900,
      cacheReadTokens: 100,
      cacheWriteTokens: 0,
      reasoningTokens: 300,
      costUsd: 0.42,
      latencyMs: 2100,
      streaming: true,
      cacheHit: false,
      timestamp: '2026-05-12T15:30:00Z',
    },
  ],
};

const MOCK_CACHE: BifrostCacheStats = {
  hits: 12,
  misses: 30,
  hitRate: 0.2857,
  savedTokens: 14000,
  savedInputTokens: 9000,
  savedOutputTokens: 5000,
  entries: 18,
};

export function createMockBifrostService(): IBifrostService {
  return {
    async listModels() {
      return [...MOCK_MODELS];
    },
    async getModelCatalog() {
      return Object.fromEntries(MOCK_MODELS.map((model) => [model.id, model]));
    },
    async listAliases() {
      return [...MOCK_ALIASES];
    },
    async listProviders() {
      return [...MOCK_PROVIDERS];
    },
    async getUsage() {
      return MOCK_USAGE;
    },
    async getCacheStats() {
      return MOCK_CACHE;
    },
  };
}
