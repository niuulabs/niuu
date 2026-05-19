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
      return 'niuu-text-brand-200 niuu-bg-bg-tertiary';
    case 'degraded':
      return 'niuu-text-warning niuu-bg-warning-bg';
    case 'unreachable':
      return 'niuu-text-critical niuu-bg-critical-bg';
    default:
      return 'niuu-text-text-muted niuu-bg-bg-tertiary';
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
      className={`niuu-inline-flex niuu-items-center niuu-gap-1.5 niuu-rounded-full niuu-px-2.5 niuu-py-1 niuu-text-[11px] niuu-font-medium ${healthTone(state)}`}
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
  const providerStateSummary = providers
    .map((provider) => `${provider.key} · ${provider.state}`)
    .join('  •  ');

  return (
    <div className="niuu-grid niuu-gap-5">
      <div className="niuu-grid niuu-grid-cols-1 md:niuu-grid-cols-2 xl:niuu-grid-cols-4 niuu-gap-4">
        {[
          {
            label: 'Enabled models',
            value: formatInteger(enabledModels),
            detail: `${formatInteger(localModels)} local · ${formatInteger(cloudModels)} cloud`,
          },
          {
            label: 'Healthy providers',
            value: `${healthyProviders}/${providers.length}`,
            detail: providers.length > 0 ? providerStateSummary : 'No providers configured',
          },
          {
            label: 'Alias routes',
            value: formatInteger(aliases.length),
            detail:
              aliases.length > 0
                ? aliases
                    .slice(0, 3)
                    .map((alias) => `${alias.alias} → ${alias.target}`)
                    .join('  •  ')
                : 'No alias routes configured',
          },
          {
            label: 'Cache hit rate',
            value: `${Math.round(cache.hitRate * 100)}%`,
            detail: `${formatInteger(cache.savedTokens)} tokens saved · ${formatInteger(cache.entries)} live entries`,
          },
        ].map((item) => (
          <section
            key={item.label}
            className="niuu-rounded-[22px] niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-p-5"
          >
            <p className="niuu-m-0 niuu-text-[11px] niuu-uppercase niuu-tracking-[0.18em] niuu-text-text-muted">
              {item.label}
            </p>
            <p className="niuu-m-0 niuu-mt-2 niuu-text-[2rem] niuu-font-semibold niuu-leading-none niuu-text-text-primary">
              {item.value}
            </p>
            <p className="niuu-m-0 niuu-mt-3 niuu-text-sm niuu-leading-6 niuu-text-text-secondary">
              {item.detail}
            </p>
          </section>
        ))}
      </div>

      <div className="niuu-grid niuu-grid-cols-1 xl:niuu-grid-cols-[1.25fr,0.75fr] niuu-gap-4">
        <section className="niuu-rounded-[22px] niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-p-5">
          <div className="niuu-flex niuu-items-center niuu-justify-between niuu-gap-3">
            <div>
              <h3 className="niuu-m-0 niuu-text-xl niuu-font-semibold niuu-text-text-primary">
                Usage snapshot
              </h3>
              <p className="niuu-m-0 niuu-mt-1 niuu-text-sm niuu-text-text-muted">
                Aggregated from the live Bifrost gateway.
              </p>
            </div>
            <Chip tone="brand">{formatCurrency(usage.summary.totalCostUsd)}</Chip>
          </div>
          <div className="niuu-grid niuu-grid-cols-2 md:niuu-grid-cols-4 niuu-gap-3 niuu-mt-5">
            {[
              ['Requests', formatInteger(usage.summary.totalRequests)],
              ['Input tokens', formatInteger(usage.summary.totalInputTokens)],
              ['Output tokens', formatInteger(usage.summary.totalOutputTokens)],
              ['Reasoning tokens', formatInteger(usage.summary.totalReasoningTokens)],
            ].map(([label, value]) => (
              <div key={label} className="niuu-rounded-xl niuu-bg-bg-tertiary niuu-p-4">
                <p className="niuu-m-0 niuu-text-[11px] niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-muted">
                  {label}
                </p>
                <p className="niuu-m-0 niuu-mt-2 niuu-text-xl niuu-font-semibold niuu-text-text-primary">
                  {value}
                </p>
              </div>
            ))}
          </div>
          <div className="niuu-grid niuu-grid-cols-1 md:niuu-grid-cols-2 niuu-gap-3 niuu-mt-3">
            {[
              ['Cost', formatCurrency(usage.summary.totalCostUsd)],
              ['Cache read tokens', formatInteger(usage.summary.totalCacheReadTokens)],
            ].map(([label, value]) => (
              <div key={label} className="niuu-rounded-xl niuu-bg-bg-tertiary niuu-p-4">
                <p className="niuu-m-0 niuu-text-[11px] niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-muted">
                  {label}
                </p>
                <p className="niuu-m-0 niuu-mt-2 niuu-text-xl niuu-font-semibold niuu-text-text-primary">
                  {value}
                </p>
              </div>
            ))}
          </div>
        </section>

        <section className="niuu-rounded-[22px] niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-p-5">
          <h3 className="niuu-m-0 niuu-text-xl niuu-font-semibold niuu-text-text-primary">Cache</h3>
          <p className="niuu-m-0 niuu-mt-1 niuu-text-sm niuu-text-text-muted">
            Current process-level savings and entry pressure.
          </p>
          <div className="niuu-grid niuu-grid-cols-2 niuu-gap-3 niuu-mt-5">
            {[
              ['Hit rate', `${Math.round(cache.hitRate * 100)}%`],
              ['Entries', formatInteger(cache.entries)],
              ['Saved tokens', formatInteger(cache.savedTokens)],
              ['Saved output', formatInteger(cache.savedOutputTokens)],
            ].map(([label, value]) => (
              <div key={label} className="niuu-rounded-xl niuu-bg-bg-tertiary niuu-p-4">
                <p className="niuu-m-0 niuu-text-[11px] niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-muted">
                  {label}
                </p>
                <p className="niuu-m-0 niuu-mt-2 niuu-text-xl niuu-font-semibold niuu-text-text-primary">
                  {value}
                </p>
              </div>
            ))}
          </div>
          <div className="niuu-rounded-xl niuu-bg-bg-tertiary niuu-p-4 niuu-mt-3">
            <p className="niuu-m-0 niuu-text-[11px] niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-muted">
              Efficiency split
            </p>
            <div className="niuu-mt-3 niuu-space-y-3">
              {[
                ['Hits', formatInteger(cache.hits)],
                ['Misses', formatInteger(cache.misses)],
                ['Saved input', formatInteger(cache.savedInputTokens)],
              ].map(([label, value]) => (
                <div
                  key={label}
                  className="niuu-flex niuu-items-center niuu-justify-between niuu-gap-3"
                >
                  <span className="niuu-text-sm niuu-text-text-secondary">{label}</span>
                  <span className="niuu-text-sm niuu-font-semibold niuu-text-text-primary">
                    {value}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </section>
      </div>
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
        <div className="niuu-min-w-0">
          <div className="niuu-flex niuu-items-center niuu-gap-2">
            <span
              className="niuu-inline-block niuu-size-2.5 niuu-rounded-full"
              style={{ backgroundColor: model.color }}
            />
            <span className="niuu-font-semibold niuu-text-text-primary">{model.name}</span>
          </div>
          <p className="niuu-m-0 niuu-mt-1 niuu-text-xs niuu-text-text-muted">
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
        <div className="niuu-flex niuu-flex-col niuu-gap-1">
          <span className="niuu-text-sm niuu-text-text-secondary">
            {model.vendor || model.provider}
          </span>
          {model.providerKeys.length > 0 ? (
            <span className="niuu-text-xs niuu-text-text-faint">
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
        <span className="niuu-text-sm niuu-text-text-secondary">{model.tier}</span>
      ),
    },
    {
      key: 'runtime',
      header: 'Runtime',
      width: '18%',
      render: (model) => (
        <span className="niuu-text-sm niuu-text-text-secondary">
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
          <div className="niuu-flex niuu-flex-wrap niuu-gap-2">
            {model.aliases.map((alias) => (
              <Chip key={alias} tone="default">
                {alias}
              </Chip>
            ))}
          </div>
        ) : (
          <span className="niuu-text-sm niuu-text-text-faint">none</span>
        ),
    },
  ];

  return (
    <div className="niuu-rounded-xl niuu-border niuu-border-border niuu-bg-bg-secondary niuu-overflow-hidden">
      <Table<BifrostModel> columns={columns} rows={models} aria-label="Model inventory" />
    </div>
  );
}

function ProvidersTab({ providers }: { providers: BifrostProvider[] }) {
  return (
    <div className="niuu-grid niuu-grid-cols-1 xl:niuu-grid-cols-2 niuu-gap-4">
      {providers.map((provider) => (
        <section
          key={provider.key}
          className="niuu-rounded-xl niuu-border niuu-border-border niuu-bg-bg-secondary niuu-p-4"
        >
          <div className="niuu-flex niuu-items-start niuu-justify-between niuu-gap-3">
            <div>
              <h3 className="niuu-m-0 niuu-text-lg niuu-font-semibold niuu-text-text-primary">
                {provider.key}
              </h3>
              <p className="niuu-m-0 niuu-mt-1 niuu-text-sm niuu-text-text-muted">
                {provider.baseUrl}
              </p>
            </div>
            <HealthPill state={provider.state} />
          </div>
          <p className="niuu-m-0 niuu-mt-3 niuu-text-sm niuu-text-text-secondary">
            {provider.detail}
          </p>
          <div className="niuu-grid niuu-grid-cols-2 niuu-gap-3 niuu-mt-4">
            <div className="niuu-rounded-lg niuu-bg-bg-tertiary niuu-p-3">
              <p className="niuu-m-0 niuu-text-[11px] niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-muted">
                Models
              </p>
              <p className="niuu-m-0 niuu-mt-2 niuu-text-lg niuu-font-semibold niuu-text-text-primary">
                {provider.modelIds.length}
              </p>
            </div>
            <div className="niuu-rounded-lg niuu-bg-bg-tertiary niuu-p-3">
              <p className="niuu-m-0 niuu-text-[11px] niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-muted">
                Timeout
              </p>
              <p className="niuu-m-0 niuu-mt-2 niuu-text-lg niuu-font-semibold niuu-text-text-primary">
                {provider.timeoutSeconds}s
              </p>
            </div>
          </div>
          <div className="niuu-flex niuu-flex-wrap niuu-gap-2 niuu-mt-4">
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
    <div className="niuu-grid niuu-gap-4">
      <section className="niuu-rounded-xl niuu-border niuu-border-border niuu-bg-bg-secondary niuu-p-4">
        <h3 className="niuu-m-0 niuu-text-lg niuu-font-semibold niuu-text-text-primary">
          Top model spend
        </h3>
        <div className="niuu-grid niuu-gap-3 niuu-mt-4">
          {Object.entries(usage.summary.byModel)
            .sort((left, right) => right[1].costUsd - left[1].costUsd)
            .slice(0, 5)
            .map(([model, bucket]) => (
              <div
                key={model}
                className="niuu-flex niuu-items-center niuu-justify-between niuu-gap-3 niuu-rounded-lg niuu-bg-bg-tertiary niuu-p-3"
              >
                <div>
                  <p className="niuu-m-0 niuu-font-semibold niuu-text-text-primary">{model}</p>
                  <p className="niuu-m-0 niuu-mt-1 niuu-text-xs niuu-text-text-muted">
                    {formatInteger(bucket.requests)} requests ·{' '}
                    {formatInteger(bucket.inputTokens + bucket.outputTokens)} tokens
                  </p>
                </div>
                <Chip tone="brand">{formatCurrency(bucket.costUsd)}</Chip>
              </div>
            ))}
        </div>
      </section>

      <section className="niuu-rounded-xl niuu-border niuu-border-border niuu-bg-bg-secondary niuu-overflow-hidden">
        <div className="niuu-flex niuu-items-center niuu-justify-between niuu-gap-3 niuu-px-4 niuu-py-3 niuu-border-b niuu-border-border">
          <h3 className="niuu-m-0 niuu-text-lg niuu-font-semibold niuu-text-text-primary">
            Recent requests
          </h3>
          <Chip tone="default">{formatInteger(usage.records.length)} shown</Chip>
        </div>
        <div className="niuu-divide-y niuu-divide-border-subtle">
          {usage.records.map((record) => (
            <div key={record.requestId} className="niuu-px-4 niuu-py-3">
              <div className="niuu-flex niuu-items-center niuu-justify-between niuu-gap-3">
                <div>
                  <p className="niuu-m-0 niuu-font-semibold niuu-text-text-primary">
                    {record.model}
                  </p>
                  <p className="niuu-m-0 niuu-mt-1 niuu-text-xs niuu-text-text-muted">
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
    <div className="niuu-flex niuu-flex-col niuu-gap-5 niuu-p-6">
      <div className="niuu-flex niuu-flex-wrap niuu-items-center niuu-justify-between niuu-gap-3">
        <div>
          <p className="niuu-m-0 niuu-text-[11px] niuu-uppercase niuu-tracking-[0.18em] niuu-text-text-muted">
            Bifröst
          </p>
          <p className="niuu-m-0 niuu-mt-1 niuu-text-sm niuu-leading-6 niuu-text-text-secondary">
            {TAB_COPY[activeTab]}
          </p>
        </div>
        <div className="niuu-flex niuu-flex-wrap niuu-gap-2">
          <Chip tone="brand">{formatInteger(models.length)} models</Chip>
          <Chip tone="default">{formatInteger(providers.length)} providers</Chip>
          <Chip tone="default">{formatInteger(aliases.length)} aliases</Chip>
        </div>
      </div>
      {loading ? (
        <div className="niuu-rounded-xl niuu-border niuu-border-border niuu-bg-bg-secondary niuu-p-6 niuu-text-text-secondary">
          Loading Bifröst…
        </div>
      ) : error ? (
        <div className="niuu-rounded-xl niuu-border niuu-border-critical niuu-bg-critical-bg niuu-p-6 niuu-text-critical">
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
