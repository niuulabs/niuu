import { buildBifrostHttpAdapter } from './http';

describe('buildBifrostHttpAdapter', () => {
  it('normalizes full model, alias, provider, usage, and cache payloads', async () => {
    const cacheStats = {
      hits: 1,
      misses: 2,
      hitRate: 0.33,
      savedTokens: 50,
      savedInputTokens: 30,
      savedOutputTokens: 20,
      entries: 1,
    };
    const client = {
      get: vi.fn(async (path: string) => {
        if (path === '/models') {
          return [
            {
              id: 'gpt-5.5',
              name: 'GPT-5.5',
              vendor: 'openai',
              provider: 'cloud',
              tier: 'frontier',
              color: '#10B981',
              description: 'Frontier model',
              cost_per_million_tokens: 10,
              vram_required: '40Gi',
              session_definition: 'skuldCodex',
              supports_tools: true,
              supports_thinking: true,
              enabled: true,
              aliases: ['frontier'],
              provider_keys: ['openai'],
            },
          ];
        }
        if (path === '/aliases') {
          return [{ alias: 'frontier', target: 'gpt-5.5' }];
        }
        if (path === '/providers/health') {
          return [
            {
              key: 'openai',
              vendor: 'openai',
              base_url: 'https://api.openai.com',
              model_ids: ['gpt-5.5'],
              timeout_seconds: 120,
              cost_per_token: 0.00001,
              state: 'healthy',
              detail: 'ready',
            },
          ];
        }
        if (path === '/v1/cache/stats') {
          return cacheStats;
        }
        return {
          summary: {
            total_requests: 1,
            total_input_tokens: 10,
            total_output_tokens: 20,
            total_cache_read_tokens: 4,
            total_cache_write_tokens: 2,
            total_reasoning_tokens: 3,
            total_cost_usd: 0.4,
            by_model: {
              'gpt-5.5': {
                requests: 1,
                input_tokens: 10,
                output_tokens: 20,
                cache_read_tokens: 4,
                cache_write_tokens: 2,
                reasoning_tokens: 3,
                cost_usd: 0.4,
              },
            },
            by_provider: {
              openai: {
                requests: 1,
                input_tokens: 10,
                output_tokens: 20,
                cache_read_tokens: 4,
                cache_write_tokens: 2,
                reasoning_tokens: 3,
                cost_usd: 0.4,
              },
            },
          },
          records: [
            {
              request_id: 'req-1',
              agent_id: 'agent-1',
              tenant_id: 'tenant-1',
              session_id: 'sess-1',
              saga_id: 'saga-1',
              model: 'gpt-5.5',
              provider: 'openai',
              input_tokens: 10,
              output_tokens: 20,
              cache_read_tokens: 4,
              cache_write_tokens: 2,
              reasoning_tokens: 3,
              cost_usd: 0.4,
              latency_ms: 1200,
              streaming: true,
              cache_hit: true,
              timestamp: '2026-05-12T23:00:00Z',
            },
          ],
        };
      }),
    } as any;

    const adapter = buildBifrostHttpAdapter(client);
    const models = await adapter.listModels();
    const catalog = await adapter.getModelCatalog();
    const aliases = await adapter.listAliases();
    const providers = await adapter.listProviders();
    const usage = await adapter.getUsage(25);
    const stats = await adapter.getCacheStats();

    expect(models).toEqual([
      expect.objectContaining({
        id: 'gpt-5.5',
        provider: 'cloud',
        tier: 'frontier',
        color: '#10B981',
        description: 'Frontier model',
        cost: 10,
        vram: '40Gi',
        sessionDefinition: 'skuldCodex',
        supportsTools: true,
        supportsThinking: true,
        enabled: true,
        aliases: ['frontier'],
        providerKeys: ['openai'],
      }),
    ]);
    expect(catalog['gpt-5.5']).toEqual(models[0]);
    expect(aliases).toEqual([{ alias: 'frontier', target: 'gpt-5.5' }]);
    expect(providers).toEqual([
      {
        key: 'openai',
        vendor: 'openai',
        baseUrl: 'https://api.openai.com',
        modelIds: ['gpt-5.5'],
        timeoutSeconds: 120,
        costPerToken: 0.00001,
        state: 'healthy',
        detail: 'ready',
      },
    ]);
    expect(usage.summary.totalRequests).toBe(1);
    expect(usage.summary.byModel['gpt-5.5']?.costUsd).toBe(0.4);
    expect(usage.summary.byProvider.openai?.cacheReadTokens).toBe(4);
    expect(usage.records).toEqual([
      {
        requestId: 'req-1',
        agentId: 'agent-1',
        tenantId: 'tenant-1',
        sessionId: 'sess-1',
        sagaId: 'saga-1',
        model: 'gpt-5.5',
        provider: 'openai',
        inputTokens: 10,
        outputTokens: 20,
        cacheReadTokens: 4,
        cacheWriteTokens: 2,
        reasoningTokens: 3,
        costUsd: 0.4,
        latencyMs: 1200,
        streaming: true,
        cacheHit: true,
        timestamp: '2026-05-12T23:00:00Z',
      },
    ]);
    expect(stats).toEqual(cacheStats);
    expect(client.get).toHaveBeenCalledWith('/v1/usage?limit=25');
  });

  it('applies defaults for sparse payloads and unknown enum values', async () => {
    const client = {
      get: vi.fn(async (path: string) => {
        if (path === '/models') {
          return [
            {
              id: 'local-dev',
              name: 'Local Dev',
              provider: 'local',
              tier: 'not-a-real-tier',
            },
            {
              id: 'mystery-cloud',
              name: 'Mystery Cloud',
              provider: 'unexpected-provider',
              tier: 'execution',
              supports_tools: false,
              supports_thinking: false,
              enabled: false,
            },
          ];
        }
        if (path === '/aliases') {
          return [];
        }
        if (path === '/providers/health') {
          return [
            {
              key: 'mystery',
              vendor: 'mystery',
              base_url: 'https://example.test',
              state: 'not-a-real-state',
            },
          ];
        }
        if (path === '/v1/cache/stats') {
          return {
            hits: 0,
            misses: 0,
            hitRate: 0,
            savedTokens: 0,
            savedInputTokens: 0,
            savedOutputTokens: 0,
            entries: 0,
          };
        }
        return {
          summary: {},
          records: [
            {
              request_id: 'req-2',
              agent_id: 'agent-2',
              tenant_id: 'tenant-2',
              session_id: 'sess-2',
              saga_id: 'saga-2',
              model: 'local-dev',
              provider: 'local',
              input_tokens: 0,
              output_tokens: 0,
              cache_read_tokens: 0,
              cache_write_tokens: 0,
              reasoning_tokens: 0,
              cost_usd: 0,
              latency_ms: 10,
              streaming: false,
              timestamp: '2026-05-12T23:10:00Z',
            },
          ],
        };
      }),
    } as any;

    const adapter = buildBifrostHttpAdapter(client);
    const models = await adapter.listModels();
    const providers = await adapter.listProviders();
    const usage = await adapter.getUsage();

    expect(models).toEqual([
      {
        id: 'local-dev',
        name: 'Local Dev',
        vendor: undefined,
        provider: 'local',
        tier: 'balanced',
        color: '#2563EB',
        description: '',
        cost: undefined,
        vram: undefined,
        sessionDefinition: undefined,
        supportsTools: true,
        supportsThinking: true,
        enabled: true,
        aliases: [],
        providerKeys: [],
      },
      {
        id: 'mystery-cloud',
        name: 'Mystery Cloud',
        vendor: undefined,
        provider: 'cloud',
        tier: 'execution',
        color: '#2563EB',
        description: '',
        cost: undefined,
        vram: undefined,
        sessionDefinition: undefined,
        supportsTools: false,
        supportsThinking: false,
        enabled: false,
        aliases: [],
        providerKeys: [],
      },
    ]);
    expect(providers).toEqual([
      {
        key: 'mystery',
        vendor: 'mystery',
        baseUrl: 'https://example.test',
        modelIds: [],
        timeoutSeconds: 0,
        costPerToken: 0,
        state: 'unknown',
        detail: '',
      },
    ]);
    expect(usage.summary).toEqual({
      totalRequests: 0,
      totalInputTokens: 0,
      totalOutputTokens: 0,
      totalCacheReadTokens: 0,
      totalCacheWriteTokens: 0,
      totalReasoningTokens: 0,
      totalCostUsd: 0,
      byModel: {},
      byProvider: {},
    });
    expect(usage.records).toEqual([
      {
        requestId: 'req-2',
        agentId: 'agent-2',
        tenantId: 'tenant-2',
        sessionId: 'sess-2',
        sagaId: 'saga-2',
        model: 'local-dev',
        provider: 'local',
        inputTokens: 0,
        outputTokens: 0,
        cacheReadTokens: 0,
        cacheWriteTokens: 0,
        reasoningTokens: 0,
        costUsd: 0,
        latencyMs: 10,
        streaming: false,
        cacheHit: false,
        timestamp: '2026-05-12T23:10:00Z',
      },
    ]);
    expect(client.get).toHaveBeenCalledWith('/v1/usage?limit=100');
  });
});
