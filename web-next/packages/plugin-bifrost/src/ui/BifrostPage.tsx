import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { Chip, StateDot, Table } from '@niuulabs/ui';
import type { TableColumn } from '@niuulabs/ui';
import type {
  BifrostAlias,
  BifrostCacheStats,
  BifrostModel,
  BifrostProvider,
  BifrostUsageResponse,
  IBifrostService,
} from '../ports';
import './BifrostPage.css';

const EMPTY_ALIASES: BifrostAlias[] = [];
const EMPTY_MODELS: BifrostModel[] = [];
const EMPTY_PROVIDERS: BifrostProvider[] = [];
const EMPTY_USAGE: BifrostUsageResponse = {
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
};
const EMPTY_CACHE: BifrostCacheStats = {
  hits: 0,
  misses: 0,
  hitRate: 0,
  savedTokens: 0,
  savedInputTokens: 0,
  savedOutputTokens: 0,
  entries: 0,
};

type BifrostTab = 'overview' | 'models' | 'providers' | 'usage';

const TAB_COPY: Record<BifrostTab, string> = {
  overview: 'Control plane snapshot of models, providers, aliases, and cache health.',
  models: 'Canonical model inventory, runtime mapping, and capability metadata.',
  providers: 'Configured upstreams with live reachability and model coverage.',
  usage: 'Recent request usage, cost, and cache effectiveness from the gateway.',
};

function formatCurrency(value: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  }).format(value);
}

function formatInteger(value: number): string {
  return new Intl.NumberFormat('en-US').format(value);
}

function healthTone(state: BifrostProvider['state']): string {
  switch (state) {
    case 'healthy':
      return 'niuu:text-brand-200 niuu:bg-bg-tertiary';
    case 'degraded':
      return 'niuu:text-warning niuu:bg-warning-bg';
    case 'unreachable':
      return 'niuu:text-critical niuu:bg-critical-bg';
    default:
      return 'niuu:text-text-muted niuu:bg-bg-tertiary';
  }
}

function HealthPill({ state }: { state: BifrostProvider['state'] }) {
  const dotState =
    state === 'healthy'
      ? 'healthy'
      : state === 'degraded'
        ? 'degraded'
        : state === 'unreachable'
          ? 'failed'
          : 'unknown';

  return (
    <span
      className={`niuu:inline-flex niuu:items-center niuu:gap-1.5 niuu:rounded-full niuu:px-2.5 niuu:py-1 niuu:text-[11px] niuu:font-medium ${healthTone(state)}`}
    >
      <StateDot state={dotState} />
      {state}
    </span>
  );
}

function OverviewTab({
  models,
  providers,
  aliases,
  usage,
  cache,
}: {
  models: BifrostModel[];
  providers: BifrostProvider[];
  aliases: BifrostAlias[];
  usage: BifrostUsageResponse;
  cache: BifrostCacheStats;
}) {
  const healthyProviders = providers.filter((provider) => provider.state === 'healthy').length;
  const enabledModels = models.filter((model) => model.enabled).length;
  const localModels = models.filter((model) => model.provider === 'local').length;
  const cloudModels = models.filter((model) => model.provider === 'cloud').length;
  const providerStateSummary = providers.length
    ? providers.map((provider) => `${provider.key}:${provider.state}`).join(' · ')
    : 'No providers configured';
  const aliasSummary = aliases.length
    ? aliases
        .slice(0, 2)
        .map((alias) => `${alias.alias}→${alias.target}`)
        .join(' · ')
    : 'No alias routes configured';

  return (
    <div className="bf-dash">
      <div className="bf-dash__topbar">
        <span className="bf-dash__stat">
          models <strong>{formatInteger(enabledModels)}</strong>
        </span>
        <span className="bf-dash__stat-sep" aria-hidden="true" />
        <span className="bf-dash__stat">
          providers{' '}
          <strong>
            {healthyProviders}/{providers.length}
          </strong>
        </span>
        <span className="bf-dash__stat-sep" aria-hidden="true" />
        <span className="bf-dash__stat">
          aliases <strong>{aliases.length}</strong>
        </span>
      </div>

      {[
        {
          label: 'Enabled models',
          value: formatInteger(enabledModels),
          detail: `${formatInteger(localModels)} local · ${formatInteger(cloudModels)} cloud`,
          accent: true,
        },
        {
          label: 'Healthy providers',
          value: `${healthyProviders}/${providers.length}`,
          detail: providerStateSummary,
        },
        {
          label: 'Alias routes',
          value: formatInteger(aliases.length),
          detail: aliasSummary,
        },
        {
          label: 'Cache hit rate',
          value: `${Math.round(cache.hitRate * 100)}%`,
          detail: `${formatInteger(cache.savedTokens)} saved · ${formatInteger(cache.entries)} entries`,
        },
      ].map((item) => (
        <section key={item.label} className={item.accent ? 'bf-kpi bf-kpi--accent' : 'bf-kpi'}>
          <div className="bf-kpi__label">{item.label}</div>
          <div className="bf-kpi__val">{item.value}</div>
          <div className="bf-kpi__sub">{item.detail}</div>
        </section>
      ))}

      <section className="bf-panel bf-panel--wide">
        <div className="bf-panel__head">
          <div>
            <h3 className="bf-panel__title">Usage snapshot</h3>
            <p className="bf-panel__copy">
              Live request, token, and spend telemetry from the gateway.
            </p>
          </div>
          <Chip tone="brand">{formatCurrency(usage.summary.totalCostUsd)}</Chip>
        </div>
        <div className="bf-panel__metric-grid">
          {[
            ['Requests', formatInteger(usage.summary.totalRequests)],
            ['Input tokens', formatInteger(usage.summary.totalInputTokens)],
            ['Output tokens', formatInteger(usage.summary.totalOutputTokens)],
            ['Reasoning tokens', formatInteger(usage.summary.totalReasoningTokens)],
          ].map(([label, value]) => (
            <div key={label} className="bf-panel__metric">
              <div className="bf-panel__metric-label">{label}</div>
              <div className="bf-panel__metric-value">{value}</div>
            </div>
          ))}
        </div>
        <div className="bf-panel__split-grid">
          {[
            ['Cost', formatCurrency(usage.summary.totalCostUsd)],
            ['Cache reads', formatInteger(usage.summary.totalCacheReadTokens)],
            ['Cache writes', formatInteger(usage.summary.totalCacheWriteTokens)],
            ['Providers billed', formatInteger(Object.keys(usage.summary.byProvider).length)],
          ].map(([label, value]) => (
            <div key={label} className="bf-panel__metric">
              <div className="bf-panel__metric-label">{label}</div>
              <div className="bf-panel__metric-value">{value}</div>
            </div>
          ))}
        </div>
      </section>

      <section className="bf-panel bf-panel--side">
        <div className="bf-panel__head">
          <div>
            <h3 className="bf-panel__title">Cache</h3>
            <p className="bf-panel__copy">Process-level hit rate, savings, and entry pressure.</p>
          </div>
        </div>
        <div className="bf-panel__metric-grid bf-panel__metric-grid--compact">
          {[
            ['Hit rate', `${Math.round(cache.hitRate * 100)}%`],
            ['Entries', formatInteger(cache.entries)],
            ['Saved tokens', formatInteger(cache.savedTokens)],
            ['Saved output', formatInteger(cache.savedOutputTokens)],
          ].map(([label, value]) => (
            <div key={label} className="bf-panel__metric">
              <div className="bf-panel__metric-label">{label}</div>
              <div className="bf-panel__metric-value">{value}</div>
            </div>
          ))}
        </div>
        <div className="bf-list-card">
          <div className="bf-list-card__label">Efficiency split</div>
          <div className="bf-list-card__rows">
            {[
              ['Hits', formatInteger(cache.hits)],
              ['Misses', formatInteger(cache.misses)],
              ['Saved input', formatInteger(cache.savedInputTokens)],
            ].map(([label, value]) => (
              <div key={label} className="bf-list-card__row">
                <span>{label}</span>
                <strong>{value}</strong>
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}

function ModelsTab({ models }: { models: BifrostModel[] }) {
  const columns: TableColumn<BifrostModel>[] = [
    {
      key: 'name',
      header: 'Model',
      width: '32%',
      render: (model) => (
        <div className="niuu:min-w-0">
          <div className="niuu:flex niuu:items-center niuu:gap-2">
            <span
              className="niuu:inline-block niuu:size-2.5 niuu:rounded-full"
              style={{ backgroundColor: model.color }}
            />
            <span className="niuu:font-semibold niuu:text-text-primary">{model.name}</span>
          </div>
          <p className="niuu:m-0 niuu:mt-1 niuu:text-xs niuu:text-text-muted">
            {model.description || model.id}
          </p>
        </div>
      ),
    },
    {
      key: 'vendor',
      header: 'Vendor',
      width: '18%',
      render: (model) => (
        <div className="niuu:flex niuu:flex-col niuu:gap-1">
          <span className="niuu:text-sm niuu:text-text-secondary">
            {model.vendor || model.provider}
          </span>
          {model.providerKeys.length > 0 ? (
            <span className="niuu:text-xs niuu:text-text-faint">
              {model.providerKeys.join(', ')}
            </span>
          ) : null}
        </div>
      ),
    },
    {
      key: 'tier',
      header: 'Tier',
      width: '12%',
      render: (model) => (
        <span className="niuu:text-sm niuu:text-text-secondary">{model.tier}</span>
      ),
    },
    {
      key: 'runtime',
      header: 'Runtime',
      width: '18%',
      render: (model) => (
        <span className="niuu:text-sm niuu:text-text-secondary">
          {model.sessionDefinition ?? 'provider default'}
        </span>
      ),
    },
    {
      key: 'aliases',
      header: 'Aliases',
      width: '20%',
      render: (model) =>
        model.aliases.length > 0 ? (
          <div className="niuu:flex niuu:flex-wrap niuu:gap-2">
            {model.aliases.map((alias) => (
              <Chip key={alias} tone="default">
                {alias}
              </Chip>
            ))}
          </div>
        ) : (
          <span className="niuu:text-sm niuu:text-text-faint">none</span>
        ),
    },
  ];

  return (
    <div className="niuu:rounded-xl niuu:border niuu:border-border niuu:bg-bg-secondary niuu:overflow-hidden">
      <Table<BifrostModel> columns={columns} rows={models} aria-label="Model inventory" />
    </div>
  );
}

function ProvidersTab({ providers }: { providers: BifrostProvider[] }) {
  return (
    <div className="niuu:grid niuu:grid-cols-1 niuu:xl:grid-cols-2 niuu:gap-4">
      {providers.map((provider) => (
        <section
          key={provider.key}
          className="niuu:rounded-xl niuu:border niuu:border-border niuu:bg-bg-secondary niuu:p-4"
        >
          <div className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-3">
            <div>
              <h3 className="niuu:m-0 niuu:text-lg niuu:font-semibold niuu:text-text-primary">
                {provider.key}
              </h3>
              <p className="niuu:m-0 niuu:mt-1 niuu:text-sm niuu:text-text-muted">
                {provider.baseUrl}
              </p>
            </div>
            <HealthPill state={provider.state} />
          </div>
          <p className="niuu:m-0 niuu:mt-3 niuu:text-sm niuu:text-text-secondary">
            {provider.detail}
          </p>
          <div className="niuu:grid niuu:grid-cols-2 niuu:gap-3 niuu:mt-4">
            <div className="niuu:rounded-lg niuu:bg-bg-tertiary niuu:p-3">
              <p className="niuu:m-0 niuu:text-[11px] niuu:uppercase niuu:tracking-[0.14em] niuu:text-text-muted">
                Models
              </p>
              <p className="niuu:m-0 niuu:mt-2 niuu:text-lg niuu:font-semibold niuu:text-text-primary">
                {provider.modelIds.length}
              </p>
            </div>
            <div className="niuu:rounded-lg niuu:bg-bg-tertiary niuu:p-3">
              <p className="niuu:m-0 niuu:text-[11px] niuu:uppercase niuu:tracking-[0.14em] niuu:text-text-muted">
                Timeout
              </p>
              <p className="niuu:m-0 niuu:mt-2 niuu:text-lg niuu:font-semibold niuu:text-text-primary">
                {provider.timeoutSeconds}s
              </p>
            </div>
          </div>
          <div className="niuu:flex niuu:flex-wrap niuu:gap-2 niuu:mt-4">
            {provider.modelIds.map((modelId) => (
              <Chip key={modelId} tone="default">
                {modelId}
              </Chip>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function UsageTab({ usage }: { usage: BifrostUsageResponse }) {
  return (
    <div className="niuu:grid niuu:gap-4">
      <section className="niuu:rounded-xl niuu:border niuu:border-border niuu:bg-bg-secondary niuu:p-4">
        <h3 className="niuu:m-0 niuu:text-lg niuu:font-semibold niuu:text-text-primary">
          Top model spend
        </h3>
        <div className="niuu:grid niuu:gap-3 niuu:mt-4">
          {Object.entries(usage.summary.byModel)
            .sort((left, right) => right[1].costUsd - left[1].costUsd)
            .slice(0, 5)
            .map(([model, bucket]) => (
              <div
                key={model}
                className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3 niuu:rounded-lg niuu:bg-bg-tertiary niuu:p-3"
              >
                <div>
                  <p className="niuu:m-0 niuu:font-semibold niuu:text-text-primary">{model}</p>
                  <p className="niuu:m-0 niuu:mt-1 niuu:text-xs niuu:text-text-muted">
                    {formatInteger(bucket.requests)} requests ·{' '}
                    {formatInteger(bucket.inputTokens + bucket.outputTokens)} tokens
                  </p>
                </div>
                <Chip tone="brand">{formatCurrency(bucket.costUsd)}</Chip>
              </div>
            ))}
        </div>
      </section>

      <section className="niuu:rounded-xl niuu:border niuu:border-border niuu:bg-bg-secondary niuu:overflow-hidden">
        <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3 niuu:px-4 niuu:py-3 niuu:border-b niuu:border-border">
          <h3 className="niuu:m-0 niuu:text-lg niuu:font-semibold niuu:text-text-primary">
            Recent requests
          </h3>
          <Chip tone="default">{formatInteger(usage.records.length)} shown</Chip>
        </div>
        <div className="niuu:divide-y niuu:divide-border-subtle">
          {usage.records.map((record) => (
            <div key={record.requestId} className="niuu:px-4 niuu:py-3">
              <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
                <div>
                  <p className="niuu:m-0 niuu:font-semibold niuu:text-text-primary">
                    {record.model}
                  </p>
                  <p className="niuu:m-0 niuu:mt-1 niuu:text-xs niuu:text-text-muted">
                    {record.provider} · {record.agentId} ·{' '}
                    {new Date(record.timestamp).toLocaleString()}
                  </p>
                </div>
                <Chip tone={record.cacheHit ? 'brand' : 'default'}>
                  {record.cacheHit ? 'cache hit' : formatCurrency(record.costUsd)}
                </Chip>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

export function BifrostPage({ defaultTab = 'overview' }: { defaultTab?: BifrostTab }) {
  const bifrost = useService<IBifrostService>('bifrost');
  const activeTab = defaultTab;
  const modelsQuery = useQuery({
    queryKey: ['bifrost', 'models'],
    queryFn: () => bifrost.listModels(),
    staleTime: 30_000,
  });
  const providersQuery = useQuery({
    queryKey: ['bifrost', 'providers'],
    queryFn: () => bifrost.listProviders(),
    staleTime: 15_000,
  });
  const aliasesQuery = useQuery({
    queryKey: ['bifrost', 'aliases'],
    queryFn: () => bifrost.listAliases(),
    staleTime: 30_000,
  });
  const usageQuery = useQuery({
    queryKey: ['bifrost', 'usage'],
    queryFn: () => bifrost.getUsage(25),
    staleTime: 15_000,
  });
  const cacheQuery = useQuery({
    queryKey: ['bifrost', 'cache'],
    queryFn: () => bifrost.getCacheStats(),
    staleTime: 15_000,
  });

  const models = modelsQuery.data ?? EMPTY_MODELS;
  const providers = providersQuery.data ?? EMPTY_PROVIDERS;
  const aliases = aliasesQuery.data ?? EMPTY_ALIASES;
  const usage = usageQuery.data ?? EMPTY_USAGE;
  const cache = cacheQuery.data ?? EMPTY_CACHE;

  const loading =
    modelsQuery.isLoading ||
    providersQuery.isLoading ||
    aliasesQuery.isLoading ||
    usageQuery.isLoading ||
    cacheQuery.isLoading;
  const error =
    modelsQuery.error ??
    providersQuery.error ??
    aliasesQuery.error ??
    usageQuery.error ??
    cacheQuery.error;

  const providerCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const model of models) {
      for (const key of model.providerKeys) {
        counts.set(key, (counts.get(key) ?? 0) + 1);
      }
    }
    return counts;
  }, [models]);

  return (
    <div className="bf-page">
      <div className="bf-page__head">
        <div>
          <p className="bf-page__title">Bifröst</p>
          <p className="bf-page__copy">{TAB_COPY[activeTab]}</p>
        </div>
        <div className="niuu:flex niuu:flex-wrap niuu:gap-2">
          <Chip tone="brand">{formatInteger(models.length)} models</Chip>
          <Chip tone="default">{formatInteger(providers.length)} providers</Chip>
          <Chip tone="default">{formatInteger(aliases.length)} aliases</Chip>
        </div>
      </div>
      {loading ? (
        <div className="niuu:rounded-xl niuu:border niuu:border-border niuu:bg-bg-secondary niuu:p-6 niuu:text-text-secondary">
          Loading Bifröst…
        </div>
      ) : error ? (
        <div className="niuu:rounded-xl niuu:border niuu:border-critical niuu:bg-critical-bg niuu:p-6 niuu:text-critical">
          {error instanceof Error ? error.message : 'Failed to load Bifröst'}
        </div>
      ) : activeTab === 'overview' ? (
        <OverviewTab
          models={models}
          providers={providers}
          aliases={aliases}
          usage={usage}
          cache={cache}
        />
      ) : activeTab === 'models' ? (
        <ModelsTab models={models} />
      ) : activeTab === 'providers' ? (
        <ProvidersTab
          providers={providers.map((provider) => ({
            ...provider,
            detail:
              provider.detail ||
              `${formatInteger(providerCounts.get(provider.key) ?? provider.modelIds.length)} models routed through this provider.`,
          }))}
        />
      ) : (
        <UsageTab usage={usage} />
      )}
    </div>
  );
}
