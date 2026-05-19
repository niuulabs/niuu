import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createMockBifrostService } from '../adapters/mock';
import { BifrostPage } from './BifrostPage';
import type { IBifrostService } from '../ports';

function renderPage(
  defaultTab: 'overview' | 'models' | 'providers' | 'usage' = 'overview',
  bifrost: IBifrostService = createMockBifrostService(),
) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ServicesProvider services={{ bifrost }}>
        <BifrostPage defaultTab={defaultTab} />
      </ServicesProvider>
    </QueryClientProvider>,
  );
}

describe('BifrostPage', () => {
  it('renders the overview metric bar and summary panels', async () => {
    renderPage();

    expect(await screen.findByText('Enabled models')).toBeInTheDocument();
    expect(screen.getByText('Cache hit rate')).toBeInTheDocument();
    expect(screen.getByText('Usage snapshot')).toBeInTheDocument();
    expect(screen.getByText('Cache')).toBeInTheDocument();
  });

  it('renders the models tab as a real table without an in-page tab row', async () => {
    renderPage('models');

    expect(await screen.findByRole('table', { name: 'Model inventory' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Model' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Runtime' })).toBeInTheDocument();
    expect(
      screen.queryAllByRole('button', { name: /overview|models|providers|usage/i }),
    ).toHaveLength(0);
  });

  it('renders model fallbacks when vendor keys and aliases are absent', async () => {
    const base = createMockBifrostService();
    const bifrost: IBifrostService = {
      ...base,
      async listModels() {
        return [
          {
            id: 'local-exec',
            name: 'Local Exec',
            provider: 'local',
            tier: 'execution',
            color: '#334155',
            description: 'Local runtime without aliases.',
            sessionDefinition: 'skuldOpenCode',
            supportsTools: true,
            supportsThinking: false,
            enabled: true,
            aliases: [],
            providerKeys: [],
          },
        ];
      },
    };

    renderPage('models', bifrost);

    expect(await screen.findByRole('table', { name: 'Model inventory' })).toBeInTheDocument();
    expect(screen.getByText('local')).toBeInTheDocument();
    expect(screen.getAllByText('none').length).toBeGreaterThan(0);
  });

  it('renders provider health states and fallback detail copy', async () => {
    const base = createMockBifrostService();
    const bifrost: IBifrostService = {
      ...base,
      async listModels() {
        return [
          {
            id: 'local-exec',
            name: 'Local Exec',
            vendor: 'local',
            provider: 'local',
            tier: 'execution',
            color: '#334155',
            description: 'Local runtime without provider keys.',
            sessionDefinition: 'skuldOpenCode',
            supportsTools: true,
            supportsThinking: false,
            enabled: true,
            aliases: [],
            providerKeys: [],
          },
        ];
      },
      async listProviders() {
        return [
          {
            key: 'local',
            vendor: 'local',
            baseUrl: 'unix:///var/run/local.sock',
            modelIds: ['local-exec'],
            timeoutSeconds: 30,
            costPerToken: 0,
            state: 'degraded',
            detail: '',
          },
          {
            key: 'backup',
            vendor: 'cloud',
            baseUrl: 'https://backup.example.com',
            modelIds: ['local-exec'],
            timeoutSeconds: 45,
            costPerToken: 0,
            state: 'unreachable',
            detail: 'Manually pinned during maintenance.',
          },
        ];
      },
    };

    renderPage('providers', bifrost);

    expect(await screen.findByText('1 models routed through this provider.')).toBeInTheDocument();
    expect(screen.getByText('degraded')).toBeInTheDocument();
    expect(screen.getByText('unreachable')).toBeInTheDocument();
    expect(screen.getByText('Manually pinned during maintenance.')).toBeInTheDocument();
  });

  it('renders usage with cache hits', async () => {
    const base = createMockBifrostService();
    const bifrost: IBifrostService = {
      ...base,
      async getUsage() {
        return {
          summary: {
            totalRequests: 2,
            totalInputTokens: 300,
            totalOutputTokens: 90,
            totalCacheReadTokens: 30,
            totalCacheWriteTokens: 10,
            totalReasoningTokens: 12,
            totalCostUsd: 0.63,
            byModel: {
              'gpt-5.5': {
                requests: 1,
                inputTokens: 200,
                outputTokens: 60,
                cacheReadTokens: 0,
                cacheWriteTokens: 10,
                reasoningTokens: 10,
                costUsd: 0.51,
              },
              'claude-sonnet-4-6': {
                requests: 1,
                inputTokens: 100,
                outputTokens: 30,
                cacheReadTokens: 30,
                cacheWriteTokens: 0,
                reasoningTokens: 2,
                costUsd: 0.12,
              },
            },
            byProvider: {
              openai: {
                requests: 1,
                inputTokens: 200,
                outputTokens: 60,
                cacheReadTokens: 0,
                cacheWriteTokens: 10,
                reasoningTokens: 10,
                costUsd: 0.51,
              },
              anthropic: {
                requests: 1,
                inputTokens: 100,
                outputTokens: 30,
                cacheReadTokens: 30,
                cacheWriteTokens: 0,
                reasoningTokens: 2,
                costUsd: 0.12,
              },
            },
          },
          records: [
            {
              requestId: 'req-cache',
              agentId: 'council-chair',
              tenantId: 'dev',
              sessionId: 'session-1',
              sagaId: 'saga-1',
              model: 'claude-sonnet-4-6',
              provider: 'anthropic',
              inputTokens: 100,
              outputTokens: 30,
              cacheReadTokens: 30,
              cacheWriteTokens: 0,
              reasoningTokens: 2,
              costUsd: 0.12,
              latencyMs: 640,
              streaming: false,
              cacheHit: true,
              timestamp: '2026-05-12T15:30:00Z',
            },
            {
              requestId: 'req-billable',
              agentId: 'warden-test',
              tenantId: 'dev',
              sessionId: 'session-2',
              sagaId: 'saga-2',
              model: 'gpt-5.5',
              provider: 'openai',
              inputTokens: 200,
              outputTokens: 60,
              cacheReadTokens: 0,
              cacheWriteTokens: 10,
              reasoningTokens: 10,
              costUsd: 0.51,
              latencyMs: 2100,
              streaming: true,
              cacheHit: false,
              timestamp: '2026-05-12T15:35:00Z',
            },
          ],
        };
      },
    };

    renderPage('usage', bifrost);

    expect(await screen.findByText('Top model spend')).toBeInTheDocument();
    expect(screen.getByText('cache hit')).toBeInTheDocument();
    expect(screen.getAllByText('$0.51').length).toBeGreaterThan(0);
    expect(screen.getByText('2 shown')).toBeInTheDocument();
  });

  it('renders an error state when Bifrost queries fail', async () => {
    const bifrost: IBifrostService = {
      listModels: async () => {
        throw new Error('catalog offline');
      },
      getModelCatalog: async () => ({}),
      listAliases: async () => [],
      listProviders: async () => [],
      getUsage: async () => ({
        summary: {
          totalRequests: 0,
          totalInputTokens: 0,
          totalOutputTokens: 0,
          totalCacheReadTokens: 0,
          totalCacheWriteTokens: 0,
          totalReasoningTokens: 0,
          totalCostUsd: 0,
          byModel: {},
          byProvider: {},
        },
        records: [],
      }),
      getCacheStats: async () => ({
        hits: 0,
        misses: 0,
        hitRate: 0,
        savedTokens: 0,
        savedInputTokens: 0,
        savedOutputTokens: 0,
        entries: 0,
      }),
    };

    renderPage('overview', bifrost);

    expect(await screen.findByText('catalog offline')).toBeInTheDocument();
  });
});
