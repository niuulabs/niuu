import { useCallback, useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouterState } from '@tanstack/react-router';
import { createApiClient } from '@niuulabs/query';
import { useConfig, useService, type IIdentityService } from '@niuulabs/plugin-sdk';
import { Dialog, DialogContent, ErrorState, LoadingState, Rune, cn } from '@niuulabs/ui';
import {
  Building2,
  ChevronRight,
  ExternalLink,
  RefreshCw,
  Search,
  Shield,
  UserRound,
  Wifi,
  WifiOff,
} from 'lucide-react';
import { resolveNiuuRegistryBase } from './services';

type InstanceKind = 'volundr' | 'ting' | 'mimir' | 'bifrost' | 'ravn' | 'observatory' | 'generic';
type RegistrySection = 'instances' | 'access' | 'connections';
type WizardStep = 1 | 2 | 3;
type AuthMethod = 'service-account' | 'personal-token' | 'oauth' | 'mtls' | 'shared-secret';
type CredentialScope = 'none' | 'user' | 'tenant';
type VisibilityScope = 'user' | 'tenant' | 'system';
type InstanceCatalogEntry = {
  kind: InstanceKind;
  label: string;
  rune: string;
  summary: string;
  detail: string;
  registerable: boolean;
  filterable: boolean;
};

type InstanceRecord = {
  id: string;
  kind: InstanceKind;
  slug: string;
  name: string;
  baseUrl: string;
  visibility: VisibilityScope | string;
  ownerId: string | null;
  tenantId: string | null;
  enabled: boolean;
  isDefault: boolean;
  config: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
};

type InstanceTestResult = {
  ok: boolean;
  statusCode?: number | null;
  message: string;
};

type HealthSnapshot = InstanceTestResult & {
  checkedAt: number;
};

type CredentialSummary = {
  name: string;
  keys: string[];
};

type CredentialListPayload = {
  credentials: CredentialSummary[];
};

type WizardState = {
  kind: InstanceKind;
  name: string;
  baseUrl: string;
  authMethod: AuthMethod;
  credentialScope: CredentialScope;
  credentialName: string;
  visibility: VisibilityScope;
};

const FILTER_KIND_OPTIONS: Array<{
  value: Exclude<InstanceKind, 'generic'>;
  label: string;
  rune: string;
}> = [
  { value: 'volundr', label: 'Volundr', rune: 'ᚲ' },
  { value: 'ting', label: 'Tyr', rune: '✦' },
  { value: 'mimir', label: 'Mimir', rune: 'ᛗ' },
  { value: 'bifrost', label: 'Bifrost', rune: 'ᚨ' },
  { value: 'ravn', label: 'Ravn', rune: 'ᚱ' },
  { value: 'observatory', label: 'Observatory', rune: 'ᛞ' },
];

const REGISTER_KIND_OPTIONS: Array<{
  value: Extract<InstanceKind, 'volundr' | 'mimir' | 'bifrost' | 'ting'>;
  label: string;
  rune: string;
  summary: string;
  detail: string;
}> = [
  {
    value: 'volundr',
    label: 'Volundr',
    rune: 'ᚲ',
    summary: 'session forge',
    detail: 'spawns remote dev pods',
  },
  {
    value: 'mimir',
    label: 'Mimir',
    rune: 'ᛗ',
    summary: 'knowledge index',
    detail: 'chronicles, embeddings, graph',
  },
  {
    value: 'bifrost',
    label: 'Bifrost',
    rune: 'ᚨ',
    summary: 'LLM gateway',
    detail: 'routes inference',
  },
  {
    value: 'ting',
    label: 'Tyr',
    rune: '✦',
    summary: 'saga coordinator',
    detail: 'dispatch ravens',
  },
];

const DEFAULT_CAPABILITIES: Record<InstanceKind, string[]> = {
  volundr: ['sessions', 'chat', 'logs', 'chronicles'],
  ting: ['dispatch', 'sagas', 'workflows', 'targets'],
  mimir: ['search', 'graph', 'embeddings', 'chronicles'],
  bifrost: ['models', 'providers', 'routing', 'usage'],
  ravn: ['operators', 'audit', 'signals', 'rooms'],
  observatory: ['topology', 'registry', 'health', 'alerts'],
  generic: ['api', 'webhooks', 'integration'],
};

const AUTH_OPTIONS: Array<{
  value: AuthMethod;
  label: string;
  helper: string;
}> = [
  {
    value: 'service-account',
    label: 'service account',
    helper: 'long-lived token, rotated quarterly',
  },
  {
    value: 'personal-token',
    label: 'personal token',
    helper: 'bind to a user-owned PAT or API key',
  },
  { value: 'oauth', label: 'oauth', helper: 'interactive or refresh-token backed access' },
  { value: 'mtls', label: 'mtls', helper: 'client certificate bound transport' },
  { value: 'shared-secret', label: 'shared secret', helper: 'shared API secret or gateway key' },
];

const SCOPE_OPTIONS: Array<{
  value: VisibilityScope;
  label: string;
  helper: string;
}> = [
  { value: 'user', label: 'personal', helper: 'visible only to the owning user' },
  { value: 'tenant', label: 'tenant', helper: 'visible inside the owning tenant' },
  { value: 'system', label: 'system', helper: 'shared infrastructure, admin-managed' },
];

function slugifyName(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64);
}

function kindMeta(kind: InstanceKind) {
  return (
    FILTER_KIND_OPTIONS.find((option) => option.value === kind) ??
    ({
      value: 'generic',
      label: 'Generic API',
      rune: 'ᛦ',
    } as const)
  );
}

function authMeta(method: AuthMethod) {
  return AUTH_OPTIONS.find((option) => option.value === method) ?? AUTH_OPTIONS[0]!;
}

function scopeBadge(instance: InstanceRecord): {
  label: string;
  icon: typeof Shield;
  tone: string;
} {
  if (instance.visibility === 'tenant') {
    return {
      label: 'tenant',
      icon: Building2,
      tone: 'niuu-border-sky-500/35 niuu-bg-sky-500/10 niuu-text-sky-200',
    };
  }
  if (instance.visibility === 'user') {
    return {
      label: 'personal',
      icon: UserRound,
      tone: 'niuu-border-violet-500/35 niuu-bg-violet-500/10 niuu-text-violet-200',
    };
  }
  return {
    label: 'system',
    icon: Shield,
    tone: 'niuu-border-emerald-500/35 niuu-bg-emerald-500/10 niuu-text-emerald-200',
  };
}

function scopeSummary(instance: InstanceRecord): string {
  if (instance.visibility === 'tenant') return instance.tenantId ?? 'tenant scope';
  if (instance.visibility === 'user') return instance.ownerId ?? 'personal scope';
  return 'shared runtime';
}

function inferRegion(instance: InstanceRecord): string {
  const configured =
    typeof instance.config.region === 'string'
      ? instance.config.region
      : typeof instance.config.location === 'string'
        ? instance.config.location
        : null;
  if (configured) return configured;
  try {
    const host = new URL(instance.baseUrl).hostname;
    if (host === '127.0.0.1' || host === 'localhost') return 'local';
    if (host.includes('.')) return host.split('.').slice(-2).join('.');
    return host;
  } catch {
    return 'unknown';
  }
}

function inferCluster(instance: InstanceRecord): string {
  const configured =
    typeof instance.config.cluster === 'string'
      ? instance.config.cluster
      : typeof instance.config.environment === 'string'
        ? instance.config.environment
        : null;
  if (configured) return configured;
  return instance.baseUrl.includes('127.0.0.1') ? 'local shell' : 'remote';
}

function inferAuth(instance: InstanceRecord): string {
  const credentialBinding =
    instance.config.credentialBinding &&
    typeof instance.config.credentialBinding === 'object' &&
    !Array.isArray(instance.config.credentialBinding)
      ? (instance.config.credentialBinding as { name?: string; scope?: string })
      : null;
  const method =
    typeof instance.config.authMethod === 'string'
      ? instance.config.authMethod
      : typeof instance.config.auth === 'string'
        ? instance.config.auth
        : null;
  if (credentialBinding?.name) {
    return `${method ?? 'credential'} • ${credentialBinding.name}`;
  }
  return method ?? 'shared proxy';
}

function inferCapabilities(instance: InstanceRecord): string[] {
  const configured = [
    ...(Array.isArray(instance.config.capabilities) ? instance.config.capabilities : []),
    ...(Array.isArray(instance.config.tags) ? instance.config.tags : []),
  ]
    .filter((value): value is string => typeof value === 'string' && value.trim().length > 0)
    .map((value) => value.trim());

  const merged = [...configured, ...DEFAULT_CAPABILITIES[instance.kind]];
  return Array.from(new Set(merged)).slice(0, 6);
}

function formatAge(iso: string | null | undefined): string {
  if (!iso) return '—';
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return '—';
  const delta = Math.max(0, Date.now() - ms);
  const minutes = Math.floor(delta / 60_000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function formatExact(iso: string | null | undefined): string {
  if (!iso) return '—';
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return '—';
  return new Date(ms).toLocaleString();
}

function openPathFor(instance: InstanceRecord): string | null {
  if (typeof window === 'undefined') {
    return instance.kind === 'volundr' ? '/volundr' : instance.baseUrl;
  }

  const params = new URLSearchParams(window.location.search);
  if (!params.has('config')) {
    params.set('config', '/config.live.json');
  }

  if (instance.kind === 'volundr') return `/volundr?${params.toString()}`;
  if (instance.kind === 'ting') return `/ting/dispatch?${params.toString()}`;
  if (instance.kind === 'mimir') return `/mimir?${params.toString()}`;
  if (instance.kind === 'bifrost') return `/bifrost?${params.toString()}`;
  if (instance.kind === 'ravn') return `/ravn?${params.toString()}`;
  if (instance.kind === 'observatory') return `/observatory?${params.toString()}`;
  return instance.baseUrl;
}

function healthTone(snapshot: HealthSnapshot | null | undefined) {
  if (!snapshot) {
    return {
      dot: 'niuu-bg-slate-500/60',
      chip: 'niuu-border-border-subtle niuu-bg-bg-elevated niuu-text-text-muted',
      label: 'unchecked',
    };
  }
  if (snapshot.ok) {
    return {
      dot: 'niuu-bg-emerald-400',
      chip: 'niuu-border-emerald-500/35 niuu-bg-emerald-500/10 niuu-text-emerald-200',
      label: 'healthy',
    };
  }
  return {
    dot: 'niuu-bg-rose-400',
    chip: 'niuu-border-rose-500/35 niuu-bg-rose-500/10 niuu-text-rose-200',
    label: 'degraded',
  };
}

function detailEvents(instance: InstanceRecord, snapshot: HealthSnapshot | null | undefined) {
  const events = [
    {
      id: `created-${instance.id}`,
      title: 'instance registered',
      detail: `${kindMeta(instance.kind).label} • ${scopeSummary(instance)}`,
      when: formatAge(instance.createdAt),
      tone: 'niuu-bg-sky-500/10 niuu-text-sky-200',
    },
    {
      id: `updated-${instance.id}`,
      title: instance.isDefault ? 'default target active' : 'metadata updated',
      detail: instance.isDefault
        ? 'used as the default target in this scope'
        : 'registry metadata changed',
      when: formatAge(instance.updatedAt),
      tone: 'niuu-bg-violet-500/10 niuu-text-violet-200',
    },
  ];
  if (snapshot) {
    events.unshift({
      id: `health-${instance.id}-${snapshot.checkedAt}`,
      title: snapshot.ok ? 'health check ok' : 'health check failed',
      detail: snapshot.message,
      when: formatAge(new Date(snapshot.checkedAt).toISOString()),
      tone: snapshot.ok
        ? 'niuu-bg-emerald-500/10 niuu-text-emerald-200'
        : 'niuu-bg-rose-500/10 niuu-text-rose-200',
    });
  }
  return events;
}

function pushHealthHistory(
  state: Record<string, HealthSnapshot[]>,
  instanceId: string,
  snapshot: HealthSnapshot,
): Record<string, HealthSnapshot[]> {
  const current = state[instanceId] ?? [];
  if (current[current.length - 1]?.checkedAt === snapshot.checkedAt) return state;
  return {
    ...state,
    [instanceId]: [...current, snapshot].slice(-24),
  };
}

function countByAuth(instances: InstanceRecord[]): Array<{ label: string; count: number }> {
  const counts = new Map<string, number>();
  for (const instance of instances) {
    const label = inferAuth(instance);
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return Array.from(counts.entries())
    .map(([label, count]) => ({ label, count }))
    .sort((left, right) => right.count - left.count)
    .slice(0, 5);
}

function HealthStrip({ history }: { history: HealthSnapshot[] }) {
  const cells = Array.from(
    { length: 24 },
    (_, index) => history[index - (24 - history.length)] ?? null,
  );

  const healthyCount = history.filter((entry) => entry.ok).length;
  const percentage =
    history.length > 0 ? Math.round((healthyCount / history.length) * 1000) / 10 : null;

  return (
    <div className="niuu-space-y-2.5">
      <div className="niuu-flex niuu-items-center niuu-justify-between niuu-text-[10px] niuu-font-mono niuu-uppercase niuu-tracking-[0.18em] niuu-text-text-faint">
        <span>Last 24h availability</span>
        <span>{percentage != null ? `${percentage}%` : 'warming up'}</span>
      </div>
      <div className="niuu-grid niuu-grid-cols-12 niuu-gap-1">
        {cells.map((snapshot, index) => (
          <span
            key={`${snapshot?.checkedAt ?? 'empty'}-${index}`}
            className={cn(
              'niuu-h-6 niuu-rounded-[5px] niuu-border',
              !snapshot && 'niuu-border-border-subtle niuu-bg-bg-elevated/80',
              snapshot?.ok && 'niuu-border-emerald-400/25 niuu-bg-emerald-300/85',
              snapshot && !snapshot.ok && 'niuu-border-rose-400/35 niuu-bg-rose-400/75',
            )}
            title={
              snapshot
                ? `${snapshot.ok ? 'healthy' : 'failed'} • ${formatExact(
                    new Date(snapshot.checkedAt).toISOString(),
                  )}`
                : 'No sample yet'
            }
          />
        ))}
      </div>
      <div className="niuu-flex niuu-items-center niuu-justify-between niuu-text-[11px] niuu-text-text-muted">
        <span>24 rolling samples</span>
        <span>
          {history.length > 0
            ? formatAge(new Date(history[history.length - 1]!.checkedAt).toISOString())
            : 'pending'}
        </span>
      </div>
    </div>
  );
}

function WizardProgress({ current }: { current: WizardStep }) {
  return (
    <div className="niuu-flex niuu-items-center niuu-gap-2">
      {[1, 2, 3].map((step, index) => (
        <div key={step} className="niuu-flex niuu-items-center niuu-gap-2">
          <span
            className={cn(
              'niuu-flex niuu-h-7 niuu-w-7 niuu-items-center niuu-justify-center niuu-rounded-full niuu-border niuu-font-mono niuu-text-[12px]',
              step === current
                ? 'niuu-border-brand niuu-bg-brand niuu-text-bg-primary'
                : step < current
                  ? 'niuu-border-brand/60 niuu-bg-brand/10 niuu-text-brand'
                  : 'niuu-border-border-subtle niuu-bg-bg-tertiary niuu-text-text-faint',
            )}
          >
            {step}
          </span>
          {index < 2 ? (
            <span
              className={cn(
                'niuu-h-px niuu-w-6 niuu-bg-border-subtle',
                step < current && 'niuu-bg-brand/70',
              )}
            />
          ) : null}
        </div>
      ))}
    </div>
  );
}

function InstanceCard({
  instance,
  selected,
  health,
  onClick,
}: {
  instance: InstanceRecord;
  selected: boolean;
  health: HealthSnapshot | null | undefined;
  onClick: () => void;
}) {
  const scope = scopeBadge(instance);
  const ScopeIcon = scope.icon;
  const tone = healthTone(health);
  const meta = kindMeta(instance.kind);

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'niuu-group niuu-flex niuu-h-full niuu-flex-col niuu-rounded-[18px] niuu-border niuu-bg-bg-secondary niuu-p-4 niuu-text-left niuu-transition-colors',
        selected
          ? 'niuu-border-brand/45 niuu-bg-[#111b24] niuu-shadow-[0_0_0_1px_rgba(83,194,255,0.12)]'
          : 'niuu-border-border-subtle hover:niuu-border-white/16',
      )}
      data-testid={`guild-instance-card-${instance.slug}`}
    >
      <div className="niuu-flex niuu-items-start niuu-gap-3">
        <div className="niuu-flex niuu-h-10 niuu-w-10 niuu-flex-shrink-0 niuu-items-center niuu-justify-center niuu-rounded-[12px] niuu-border niuu-border-white/10 niuu-bg-bg-tertiary">
          <Rune glyph={meta.rune} size={16} className="niuu-text-brand" />
        </div>
        <div className="niuu-min-w-0 niuu-flex-1">
          <div className="niuu-flex niuu-items-center niuu-gap-2">
            <span className={cn('niuu-h-2 niuu-w-2 niuu-rounded-full', tone.dot)} />
            <h3 className="niuu-truncate niuu-text-[15px] niuu-font-semibold niuu-text-text-primary">
              {instance.name}
            </h3>
            {instance.isDefault ? (
              <span className="niuu-rounded-md niuu-bg-brand/12 niuu-px-1.5 niuu-py-0.5 niuu-font-mono niuu-text-[9px] niuu-uppercase niuu-tracking-[0.16em] niuu-text-brand">
                default
              </span>
            ) : null}
          </div>
          <div className="niuu-mt-1 niuu-flex niuu-flex-wrap niuu-items-center niuu-gap-1.5 niuu-text-[11px] niuu-text-text-muted">
            <span>{meta.label}</span>
            <span>•</span>
            <span>{formatAge(instance.updatedAt)}</span>
          </div>
        </div>
      </div>

      <div className="niuu-mt-3 niuu-rounded-xl niuu-bg-bg-tertiary niuu-px-3 niuu-py-2 niuu-font-mono niuu-text-[11px] niuu-text-text-secondary">
        {instance.baseUrl}
      </div>

      <div className="niuu-mt-3 niuu-flex niuu-flex-wrap niuu-items-center niuu-gap-2">
        <span
          className={cn(
            'niuu-inline-flex niuu-items-center niuu-gap-1.5 niuu-rounded-full niuu-border niuu-px-2 niuu-py-1 niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.14em]',
            scope.tone,
          )}
        >
          <ScopeIcon className="niuu-h-3 niuu-w-3" />
          {scope.label}
        </span>
        <span className="niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-faint">
          {inferRegion(instance)}
        </span>
      </div>

      <div className="niuu-mt-3 niuu-grid niuu-grid-cols-2 niuu-gap-x-3 niuu-gap-y-2 niuu-text-[11px]">
        <div>
          <div className="niuu-font-mono niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-faint">
            scope
          </div>
          <div className="niuu-mt-1 niuu-text-text-secondary">{scopeSummary(instance)}</div>
        </div>
        <div>
          <div className="niuu-font-mono niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-faint">
            auth
          </div>
          <div className="niuu-mt-1 niuu-truncate niuu-text-text-secondary">
            {inferAuth(instance)}
          </div>
        </div>
      </div>

      <div className="niuu-mt-3 niuu-flex niuu-flex-wrap niuu-gap-1.5">
        {inferCapabilities(instance).map((capability) => (
          <span
            key={`${instance.id}-${capability}`}
            className="niuu-rounded-md niuu-bg-bg-elevated niuu-px-1.5 niuu-py-1 niuu-font-mono niuu-text-[10px] niuu-text-text-muted"
          >
            {capability}
          </span>
        ))}
      </div>
    </button>
  );
}

function GuildDetailRail({
  instance,
  health,
  history,
  onTest,
  onRefresh,
  onCollapse,
  isTesting,
}: {
  instance: InstanceRecord;
  health: HealthSnapshot | null | undefined;
  history: HealthSnapshot[];
  onTest: () => void;
  onRefresh: () => void;
  onCollapse: () => void;
  isTesting: boolean;
}) {
  const meta = kindMeta(instance.kind);
  const tone = healthTone(health);
  const events = detailEvents(instance, health);
  const openPath = openPathFor(instance);

  return (
    <aside className="niuu-sticky niuu-top-6 niuu-h-fit">
      <div className="niuu-rounded-[22px] niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-p-5">
        <div className="niuu-flex niuu-items-start niuu-gap-3">
          <div className="niuu-flex niuu-h-10 niuu-w-10 niuu-flex-shrink-0 niuu-items-center niuu-justify-center niuu-rounded-[12px] niuu-border niuu-border-white/10 niuu-bg-bg-tertiary">
            <Rune glyph={meta.rune} size={16} className="niuu-text-brand" />
          </div>
          <div className="niuu-min-w-0 niuu-flex-1">
            <div className="niuu-flex niuu-items-center niuu-gap-2">
              <span className={cn('niuu-h-2 niuu-w-2 niuu-rounded-full', tone.dot)} />
              <h2 className="niuu-truncate niuu-text-[18px] niuu-font-semibold niuu-text-text-primary">
                {instance.name}
              </h2>
            </div>
            <div className="niuu-mt-1 niuu-flex niuu-flex-wrap niuu-items-center niuu-gap-2 niuu-text-[11px] niuu-text-text-muted">
              <span>{meta.label}</span>
              <span>•</span>
              <span>{instance.slug}</span>
            </div>
          </div>
          <button
            type="button"
            onClick={onCollapse}
            className="niuu-inline-flex niuu-h-8 niuu-w-8 niuu-items-center niuu-justify-center niuu-rounded-lg niuu-border niuu-border-border-subtle niuu-bg-bg-tertiary niuu-text-text-muted hover:niuu-text-text-primary"
            aria-label="Collapse details"
          >
            <ChevronRight className="niuu-h-4 niuu-w-4" />
          </button>
        </div>

        <div className="niuu-mt-5">
          <HealthStrip history={history} />
        </div>

        <dl className="niuu-mt-5 niuu-grid niuu-grid-cols-[86px_minmax(0,1fr)] niuu-gap-x-4 niuu-gap-y-2.5 niuu-text-[13px]">
          <dt className="niuu-font-mono niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-faint">
            endpoint
          </dt>
          <dd className="niuu-font-mono niuu-break-all niuu-text-text-primary">
            {instance.baseUrl}
          </dd>

          <dt className="niuu-font-mono niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-faint">
            region
          </dt>
          <dd className="niuu-text-text-primary">{inferRegion(instance)}</dd>

          <dt className="niuu-font-mono niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-faint">
            cluster
          </dt>
          <dd className="niuu-text-text-primary">{inferCluster(instance)}</dd>

          <dt className="niuu-font-mono niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-faint">
            auth
          </dt>
          <dd className="niuu-text-text-primary">{inferAuth(instance)}</dd>

          <dt className="niuu-font-mono niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-faint">
            scope
          </dt>
          <dd className="niuu-text-text-primary">{scopeSummary(instance)}</dd>

          <dt className="niuu-font-mono niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-faint">
            created
          </dt>
          <dd className="niuu-text-text-primary">{formatAge(instance.createdAt)}</dd>

          <dt className="niuu-font-mono niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-faint">
            last seen
          </dt>
          <dd className="niuu-text-text-primary">
            {health ? formatAge(new Date(health.checkedAt).toISOString()) : 'not checked'}
          </dd>
        </dl>

        <div className="niuu-mt-5">
          <div className="niuu-mb-2 niuu-flex niuu-items-center niuu-justify-between">
            <h3 className="niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.18em] niuu-text-text-faint">
              capabilities
            </h3>
            <span className="niuu-text-[10px] niuu-text-text-muted">
              {inferCapabilities(instance).length} tags
            </span>
          </div>
          <div className="niuu-flex niuu-flex-wrap niuu-gap-1.5">
            {inferCapabilities(instance).map((capability) => (
              <span
                key={`${instance.id}-${capability}`}
                className="niuu-rounded-md niuu-bg-bg-elevated niuu-px-1.5 niuu-py-1 niuu-font-mono niuu-text-[10px] niuu-text-text-secondary"
              >
                {capability}
              </span>
            ))}
          </div>
        </div>

        <div className="niuu-mt-5">
          <div className="niuu-mb-2 niuu-flex niuu-items-center niuu-justify-between">
            <h3 className="niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.18em] niuu-text-text-faint">
              recent connectivity
            </h3>
            <span className="niuu-text-[10px] niuu-text-text-muted">{events.length} events</span>
          </div>
          <div className="niuu-space-y-2">
            {events.map((event) => (
              <div
                key={event.id}
                className="niuu-flex niuu-items-start niuu-justify-between niuu-gap-3 niuu-rounded-xl niuu-bg-bg-elevated niuu-p-2.5"
              >
                <div className="niuu-min-w-0">
                  <div
                    className={cn(
                      'niuu-inline-flex niuu-rounded-full niuu-px-2 niuu-py-1 niuu-font-mono niuu-text-[9px] niuu-uppercase niuu-tracking-[0.14em]',
                      event.tone,
                    )}
                  >
                    {event.title}
                  </div>
                  <p className="niuu-mt-1.5 niuu-text-[12px] niuu-leading-5 niuu-text-text-primary">
                    {event.detail}
                  </p>
                </div>
                <span className="niuu-flex-shrink-0 niuu-text-[10px] niuu-text-text-muted">
                  {event.when}
                </span>
              </div>
            ))}
          </div>
        </div>

        <div className="niuu-mt-5 niuu-flex niuu-items-center niuu-justify-between niuu-gap-3">
          <div className="niuu-flex niuu-gap-2">
            <button
              type="button"
              onClick={onTest}
              disabled={isTesting}
              className="niuu-inline-flex niuu-items-center niuu-gap-2 niuu-rounded-lg niuu-border niuu-border-border-subtle niuu-bg-bg-elevated niuu-px-3 niuu-py-2 niuu-text-[12px] niuu-text-text-primary hover:niuu-bg-bg-tertiary disabled:niuu-opacity-50"
            >
              {health?.ok ? (
                <Wifi className="niuu-h-4 niuu-w-4" />
              ) : (
                <WifiOff className="niuu-h-4 niuu-w-4" />
              )}
              test endpoint
            </button>
            <button
              type="button"
              onClick={onRefresh}
              className="niuu-inline-flex niuu-items-center niuu-gap-2 niuu-rounded-lg niuu-border niuu-border-border-subtle niuu-bg-bg-elevated niuu-px-3 niuu-py-2 niuu-text-[12px] niuu-text-text-primary hover:niuu-bg-bg-tertiary"
            >
              <RefreshCw className="niuu-h-4 niuu-w-4" />
              refresh
            </button>
          </div>
          {openPath ? (
            <a
              href={openPath}
              target={
                instance.kind === 'volundr' || instance.kind === 'ting' ? undefined : '_blank'
              }
              rel={
                instance.kind === 'volundr' || instance.kind === 'ting' ? undefined : 'noreferrer'
              }
              className="niuu-inline-flex niuu-items-center niuu-gap-2 niuu-rounded-lg niuu-border niuu-border-brand/35 niuu-bg-brand/12 niuu-px-3 niuu-py-2 niuu-text-[12px] niuu-text-brand hover:niuu-bg-brand/18"
            >
              open in {meta.label}
              <ExternalLink className="niuu-h-4 niuu-w-4" />
            </a>
          ) : null}
        </div>
      </div>
    </aside>
  );
}

function RegisterWizard({
  open,
  onOpenChange,
  registerOptions,
  currentIdentity,
  canCreateSystemScope,
  canCreateTenantScope,
  wizard,
  setWizard,
  userCredentials,
  tenantCredentials,
  createMutationPending,
  createError,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  registerOptions: Array<{
    value: InstanceKind;
    label: string;
    rune: string;
    summary: string;
    detail: string;
  }>;
  currentIdentity: { userId?: string; tenantId?: string } | undefined;
  canCreateSystemScope: boolean;
  canCreateTenantScope: boolean;
  wizard: WizardState;
  setWizard: React.Dispatch<React.SetStateAction<WizardState>>;
  userCredentials: CredentialSummary[];
  tenantCredentials: CredentialSummary[];
  createMutationPending: boolean;
  createError: unknown;
  onSubmit: () => void;
}) {
  const [step, setStep] = useState<WizardStep>(1);

  const slug = slugifyName(wizard.name);
  const selectedAuth = authMeta(wizard.authMethod);
  const availableCredentials =
    wizard.credentialScope === 'tenant'
      ? tenantCredentials
      : wizard.credentialScope === 'user'
        ? userCredentials
        : [];

  const stepTwoValid =
    wizard.name.trim().length > 0 &&
    slug.length > 0 &&
    (() => {
      try {
        const url = new URL(wizard.baseUrl);
        return url.protocol === 'http:' || url.protocol === 'https:';
      } catch {
        return false;
      }
    })() &&
    (wizard.credentialScope === 'none' || wizard.credentialName.trim().length > 0);

  const stepThreeValid =
    wizard.visibility === 'user' ||
    (wizard.visibility === 'tenant' && canCreateTenantScope) ||
    (wizard.visibility === 'system' && canCreateSystemScope);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Register a new instance" className="niuu-max-w-[660px]">
        <div className="niuu-space-y-5">
          <div className="niuu-font-mono niuu-text-[11px] niuu-uppercase niuu-tracking-[0.18em] niuu-text-text-faint">
            {step === 1 && 'Step 1 of 3 • Backend type'}
            {step === 2 && 'Step 2 of 3 • Endpoint & auth'}
            {step === 3 && 'Step 3 of 3 • Visibility & confirm'}
          </div>

          {step === 1 ? (
            <div className="niuu-space-y-4">
              <div className="niuu-grid niuu-gap-3 sm:niuu-grid-cols-2">
                {registerOptions.map((option) => {
                  const selected = wizard.kind === option.value;
                  return (
                    <button
                      key={option.value}
                      type="button"
                      onClick={() => setWizard((current) => ({ ...current, kind: option.value }))}
                      className={cn(
                        'niuu-flex niuu-items-center niuu-gap-4 niuu-rounded-[12px] niuu-border niuu-bg-bg-tertiary niuu-p-4 niuu-text-left niuu-transition-colors',
                        selected
                          ? 'niuu-border-brand niuu-bg-[#1d2832]'
                          : 'niuu-border-transparent hover:niuu-border-white/10',
                      )}
                    >
                      <div className="niuu-flex niuu-h-11 niuu-w-11 niuu-items-center niuu-justify-center niuu-rounded-[10px] niuu-border niuu-border-white/15 niuu-bg-[#22303b]">
                        <Rune glyph={option.rune} size={18} className="niuu-text-brand" />
                      </div>
                      <div className="niuu-min-w-0">
                        <div className="niuu-flex niuu-items-center niuu-gap-2">
                          <div className="niuu-text-[15px] niuu-font-semibold niuu-text-text-primary">
                            {option.label}
                          </div>
                          {selected ? (
                            <span className="niuu-rounded-full niuu-border niuu-border-brand/45 niuu-bg-brand/12 niuu-px-2 niuu-py-0.5 niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.14em] niuu-text-brand">
                              selected
                            </span>
                          ) : null}
                        </div>
                        <div className="niuu-font-mono niuu-text-[12px] niuu-text-text-muted">
                          {option.summary} — {option.detail}
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
              <p className="niuu-font-mono niuu-text-[12px] niuu-leading-5 niuu-text-text-faint">
                Future types plug in here. Guild is a generic Niuu runtime registry, not a
                Volundr-only catalog.
              </p>
            </div>
          ) : null}

          {step === 2 ? (
            <div className="niuu-space-y-4">
              <div className="niuu-space-y-1.5">
                <label className="niuu-block niuu-text-[13px] niuu-font-medium niuu-text-text-secondary">
                  name
                </label>
                <input
                  value={wizard.name}
                  onChange={(event) =>
                    setWizard((current) => ({ ...current, name: event.target.value }))
                  }
                  placeholder="volundr • prod"
                  className="niuu-w-full niuu-rounded-xl niuu-border niuu-border-border-subtle niuu-bg-bg-tertiary niuu-px-3 niuu-py-2.5 niuu-text-[14px] niuu-text-text-primary placeholder:niuu-text-text-muted focus:niuu-outline-none"
                />
                <div className="niuu-font-mono niuu-text-[11px] niuu-text-text-faint">
                  short identifier → {slug || 'lowercase, words separated by -'}
                </div>
              </div>

              <div className="niuu-space-y-1.5">
                <label className="niuu-block niuu-text-[13px] niuu-font-medium niuu-text-text-secondary">
                  endpoint URL
                </label>
                <input
                  value={wizard.baseUrl}
                  onChange={(event) =>
                    setWizard((current) => ({ ...current, baseUrl: event.target.value }))
                  }
                  placeholder="https://..."
                  className="niuu-w-full niuu-rounded-xl niuu-border niuu-border-border-subtle niuu-bg-bg-tertiary niuu-px-3 niuu-py-2.5 niuu-text-[14px] niuu-text-text-primary placeholder:niuu-text-text-muted focus:niuu-outline-none"
                />
                <div className="niuu-font-mono niuu-text-[11px] niuu-text-text-faint">
                  Niuu probes this on register and then keeps checking it afterwards.
                </div>
              </div>

              <div className="niuu-space-y-2">
                <div className="niuu-text-[13px] niuu-font-medium niuu-text-text-secondary">
                  auth method
                </div>
                <div className="niuu-grid niuu-gap-2 sm:niuu-grid-cols-5">
                  {AUTH_OPTIONS.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      onClick={() =>
                        setWizard((current) => ({
                          ...current,
                          authMethod: option.value,
                        }))
                      }
                      className={cn(
                        'niuu-rounded-lg niuu-border niuu-px-3 niuu-py-2 niuu-text-[12px] niuu-transition-colors',
                        wizard.authMethod === option.value
                          ? 'niuu-border-brand/50 niuu-bg-brand/12 niuu-text-brand'
                          : 'niuu-border-border-subtle niuu-bg-bg-tertiary niuu-text-text-muted hover:niuu-text-text-primary',
                      )}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
                <div className="niuu-font-mono niuu-text-[11px] niuu-text-text-faint">
                  {selectedAuth.helper}
                </div>
              </div>

              <div className="niuu-space-y-2 niuu-rounded-[14px] niuu-border niuu-border-border-subtle niuu-bg-bg-tertiary niuu-p-4">
                <div className="niuu-flex niuu-items-center niuu-justify-between niuu-gap-3">
                  <div>
                    <div className="niuu-text-[13px] niuu-font-medium niuu-text-text-primary">
                      credential binding
                    </div>
                    <div className="niuu-text-[12px] niuu-text-text-muted">
                      Attach an existing secret from the integrations / auth system.
                    </div>
                  </div>
                  <a
                    href="/settings/credentials/user"
                    className="niuu-font-mono niuu-text-[11px] niuu-uppercase niuu-tracking-[0.14em] niuu-text-brand"
                  >
                    manage credentials
                  </a>
                </div>

                <div className="niuu-flex niuu-flex-wrap niuu-gap-2">
                  {[
                    { value: 'none', label: 'none' },
                    { value: 'user', label: 'personal' },
                    { value: 'tenant', label: 'tenant' },
                  ].map((option) => {
                    const disabled = (option.value === 'tenant' && !canCreateTenantScope) || false;
                    return (
                      <button
                        key={option.value}
                        type="button"
                        disabled={disabled}
                        onClick={() =>
                          setWizard((current) => ({
                            ...current,
                            credentialScope: option.value as CredentialScope,
                            credentialName: option.value === 'none' ? '' : current.credentialName,
                          }))
                        }
                        className={cn(
                          'niuu-rounded-full niuu-border niuu-px-3 niuu-py-1.5 niuu-font-mono niuu-text-[11px] niuu-uppercase niuu-tracking-[0.14em]',
                          wizard.credentialScope === option.value
                            ? 'niuu-border-brand/50 niuu-bg-brand/12 niuu-text-brand'
                            : 'niuu-border-border-subtle niuu-bg-bg-secondary niuu-text-text-muted',
                          disabled && 'niuu-cursor-not-allowed niuu-opacity-40',
                        )}
                      >
                        {option.label}
                      </button>
                    );
                  })}
                </div>

                {wizard.credentialScope !== 'none' ? (
                  <div className="niuu-space-y-1.5">
                    <label className="niuu-block niuu-text-[12px] niuu-font-medium niuu-text-text-secondary">
                      {wizard.credentialScope === 'tenant'
                        ? 'tenant credential'
                        : 'personal credential'}
                    </label>
                    <select
                      value={wizard.credentialName}
                      onChange={(event) =>
                        setWizard((current) => ({
                          ...current,
                          credentialName: event.target.value,
                        }))
                      }
                      className="niuu-w-full niuu-rounded-xl niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-3 niuu-py-2.5 niuu-text-[13px] niuu-text-text-primary"
                    >
                      <option value="">Select a credential…</option>
                      {availableCredentials.map((credential) => (
                        <option key={credential.name} value={credential.name}>
                          {credential.name}
                        </option>
                      ))}
                    </select>
                    <div className="niuu-font-mono niuu-text-[11px] niuu-text-text-faint">
                      {availableCredentials.length > 0
                        ? `${availableCredentials.length} reusable credential${availableCredentials.length === 1 ? '' : 's'} available`
                        : 'No saved credentials in this scope yet.'}
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}

          {step === 3 ? (
            <div className="niuu-space-y-4">
              <div className="niuu-space-y-2">
                <div className="niuu-text-[13px] niuu-font-medium niuu-text-text-secondary">
                  scope
                </div>
                <div className="niuu-grid niuu-gap-3 sm:niuu-grid-cols-3">
                  {SCOPE_OPTIONS.map((option) => {
                    const disabled =
                      (option.value === 'tenant' && !canCreateTenantScope) ||
                      (option.value === 'system' && !canCreateSystemScope);
                    return (
                      <button
                        key={option.value}
                        type="button"
                        disabled={disabled}
                        onClick={() =>
                          setWizard((current) => ({
                            ...current,
                            visibility: option.value,
                          }))
                        }
                        className={cn(
                          'niuu-rounded-[12px] niuu-border niuu-bg-bg-tertiary niuu-p-4 niuu-text-left niuu-transition-colors',
                          wizard.visibility === option.value
                            ? 'niuu-border-brand niuu-bg-[#1d2832]'
                            : 'niuu-border-transparent hover:niuu-border-white/10',
                          disabled && 'niuu-cursor-not-allowed niuu-opacity-40',
                        )}
                      >
                        <div className="niuu-inline-flex niuu-rounded-md niuu-border niuu-border-brand/35 niuu-bg-brand/10 niuu-px-2 niuu-py-1 niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.14em] niuu-text-brand">
                          {option.label}
                        </div>
                        <p className="niuu-mt-2 niuu-text-[12px] niuu-leading-5 niuu-text-text-secondary">
                          {option.helper}
                        </p>
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="niuu-rounded-[14px] niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-p-4">
                <dl className="niuu-grid niuu-grid-cols-[92px_minmax(0,1fr)] niuu-gap-x-4 niuu-gap-y-2 niuu-text-[13px]">
                  <dt className="niuu-font-mono niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-faint">
                    type
                  </dt>
                  <dd className="niuu-text-text-primary">{kindMeta(wizard.kind).label}</dd>

                  <dt className="niuu-font-mono niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-faint">
                    name
                  </dt>
                  <dd className="niuu-text-text-primary">{wizard.name || '—'}</dd>

                  <dt className="niuu-font-mono niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-faint">
                    endpoint
                  </dt>
                  <dd className="niuu-break-all niuu-text-text-primary">{wizard.baseUrl || '—'}</dd>

                  <dt className="niuu-font-mono niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-faint">
                    auth
                  </dt>
                  <dd className="niuu-text-text-primary">
                    {selectedAuth.label}
                    {wizard.credentialName ? ` • ${wizard.credentialName}` : ''}
                  </dd>

                  <dt className="niuu-font-mono niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-faint">
                    scope
                  </dt>
                  <dd>
                    <span className="niuu-inline-flex niuu-rounded-md niuu-border niuu-border-brand/35 niuu-bg-brand/10 niuu-px-2 niuu-py-1 niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.14em] niuu-text-brand">
                      {SCOPE_OPTIONS.find((option) => option.value === wizard.visibility)?.label}
                    </span>
                  </dd>

                  <dt className="niuu-font-mono niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-faint">
                    owner
                  </dt>
                  <dd className="niuu-text-text-primary">
                    {wizard.visibility === 'tenant'
                      ? (currentIdentity?.tenantId ?? 'current tenant')
                      : wizard.visibility === 'system'
                        ? 'system'
                        : (currentIdentity?.userId ?? 'current user')}
                  </dd>
                </dl>
              </div>

              <p className="niuu-font-mono niuu-text-[11px] niuu-leading-5 niuu-text-text-faint">
                Niuu will probe the endpoint, persist this entry in the registry, and notify
                dependent modules. No traffic flows until probe succeeds.
              </p>
            </div>
          ) : null}

          {createError ? (
            <p className="niuu-text-sm niuu-text-rose-400">
              {createError instanceof Error ? createError.message : 'Failed to register instance.'}
            </p>
          ) : null}

          <div className="niuu-flex niuu-items-center niuu-justify-between niuu-gap-3 niuu-border-t niuu-border-border-subtle niuu-pt-4">
            <WizardProgress current={step} />

            <div className="niuu-flex niuu-items-center niuu-gap-2">
              {step > 1 ? (
                <button
                  type="button"
                  onClick={() => setStep((current) => Math.max(1, current - 1) as WizardStep)}
                  className="niuu-rounded-lg niuu-border niuu-border-border-subtle niuu-bg-bg-tertiary niuu-px-3 niuu-py-2 niuu-text-[12px] niuu-font-medium niuu-text-text-primary"
                >
                  back
                </button>
              ) : null}

              {step < 3 ? (
                <button
                  type="button"
                  disabled={(step === 2 && !stepTwoValid) || createMutationPending}
                  onClick={() => setStep((current) => Math.min(3, current + 1) as WizardStep)}
                  className="niuu-rounded-lg niuu-border niuu-border-brand/45 niuu-bg-brand/12 niuu-px-3 niuu-py-2 niuu-text-[12px] niuu-font-medium niuu-text-brand disabled:niuu-opacity-40"
                >
                  next
                </button>
              ) : (
                <button
                  type="button"
                  disabled={!stepTwoValid || !stepThreeValid || createMutationPending}
                  onClick={onSubmit}
                  className="niuu-rounded-lg niuu-border niuu-border-brand niuu-bg-brand niuu-px-3 niuu-py-2 niuu-text-[12px] niuu-font-medium niuu-text-bg-primary disabled:niuu-opacity-40"
                >
                  {createMutationPending ? 'registering…' : 'register'}
                </button>
              )}
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function GuildPage() {
  const config = useConfig();
  const identity = useService<IIdentityService>('identity');
  const queryClient = useQueryClient();
  const sharedBase = resolveNiuuRegistryBase(config);
  const client = useMemo(() => {
    if (!sharedBase) return null;
    return createApiClient(sharedBase);
  }, [sharedBase]);

  const identityQuery = useQuery({
    queryKey: ['guild-identity'],
    queryFn: () => identity.getIdentity(),
  });

  const currentIdentity = identityQuery.data;
  const canCreateTenantScope = Boolean(currentIdentity?.tenantId);
  const canCreateSystemScope = currentIdentity?.roles.includes('volundr:admin') ?? false;

  const makeDefaultWizard = useCallback(
    (): WizardState => ({
      kind: 'volundr',
      name: '',
      baseUrl: '',
      authMethod: 'service-account',
      credentialScope: 'none',
      credentialName: '',
      visibility: canCreateTenantScope ? 'tenant' : 'user',
    }),
    [canCreateTenantScope],
  );

  const location = useRouterState({ select: (state) => state.location });
  const pathname = location.pathname;
  const section: RegistrySection = pathname.endsWith('/access')
    ? 'access'
    : pathname.endsWith('/connections')
      ? 'connections'
      : 'instances';
  const [search, setSearch] = useState('');
  const [kindFilter, setKindFilter] = useState<'all' | InstanceKind>('all');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(true);
  const [manualRegisterOpen, setManualRegisterOpen] = useState(false);
  const [wizard, setWizard] = useState<WizardState>(makeDefaultWizard);
  const [healthById, setHealthById] = useState<Record<string, HealthSnapshot>>({});
  const [healthHistory, setHealthHistory] = useState<Record<string, HealthSnapshot[]>>({});
  const registerRequested = useMemo(() => {
    const params = new URLSearchParams(location.search);
    return params.get('register') === '1';
  }, [location.search]);

  const resetWizard = useCallback(() => {
    setWizard(makeDefaultWizard());
  }, [makeDefaultWizard]);
  const registerOpen = manualRegisterOpen || registerRequested;

  const instancesQuery = useQuery({
    queryKey: ['guild-instances'],
    enabled: client != null,
    queryFn: () => client!.get<InstanceRecord[]>('/instances'),
    refetchInterval: 10_000,
  });
  const catalogQuery = useQuery({
    queryKey: ['guild-instance-catalog'],
    enabled: client != null,
    queryFn: () => client!.get<InstanceCatalogEntry[]>('/instances/catalog'),
  });

  const userCredentialsQuery = useQuery({
    queryKey: ['guild-user-credentials'],
    enabled: client != null,
    queryFn: async () => {
      const payload = await client!.get<CredentialListPayload>('/credentials/user');
      return payload.credentials ?? [];
    },
  });

  const tenantCredentialsQuery = useQuery({
    queryKey: ['guild-tenant-credentials', currentIdentity?.tenantId ?? 'none'],
    enabled: client != null && canCreateTenantScope,
    queryFn: async () => {
      const payload = await client!.get<CredentialListPayload>('/credentials/tenant');
      return payload.credentials ?? [];
    },
  });

  const createMutation = useMutation({
    mutationFn: () =>
      client!.post<InstanceRecord>('/instances', {
        kind: effectiveWizard.kind,
        slug: slugifyName(wizard.name),
        name: wizard.name.trim(),
        baseUrl: wizard.baseUrl.trim(),
        visibility: effectiveWizard.visibility,
        tenantId:
          effectiveWizard.visibility === 'tenant' ? currentIdentity?.tenantId : undefined,
        config: {
          source: 'guild',
          authMethod: authMeta(wizard.authMethod).label,
          auth: authMeta(wizard.authMethod).label,
          credentialBinding:
            wizard.credentialScope === 'none'
              ? null
              : {
                  scope: wizard.credentialScope,
                  name: wizard.credentialName,
                },
          capabilities: DEFAULT_CAPABILITIES[effectiveWizard.kind],
        },
      }),
    onSuccess: async (instance) => {
      setManualRegisterOpen(false);
      resetWizard();
      setSelectedId(instance.id);
      setDetailOpen(true);
      await queryClient.invalidateQueries({ queryKey: ['guild-instances'] });
    },
  });

  const healthMutation = useMutation({
    mutationFn: async (instanceId: string) =>
      client!.post<InstanceTestResult>(`/instances/${instanceId}/test`),
    onSuccess: (result, instanceId) => {
      const snapshot: HealthSnapshot = { ...result, checkedAt: Date.now() };
      setHealthById((current) => ({ ...current, [instanceId]: snapshot }));
      setHealthHistory((current) => pushHealthHistory(current, instanceId, snapshot));
    },
  });

  const instances = useMemo(() => instancesQuery.data ?? [], [instancesQuery.data]);
  const catalogEntries = useMemo(() => catalogQuery.data ?? [], [catalogQuery.data]);
  const filterOptions = useMemo(
    () =>
      catalogEntries.length > 0
        ? catalogEntries.filter((entry) => entry.filterable)
        : FILTER_KIND_OPTIONS.map((option) => ({
            kind: option.value,
            label: option.label,
            rune: option.rune,
            summary: '',
            detail: '',
            registerable: true,
            filterable: true,
          })),
    [catalogEntries],
  );
  const registerOptions = useMemo(
    () =>
      catalogEntries.length > 0
        ? catalogEntries.filter((entry) => entry.registerable)
        : REGISTER_KIND_OPTIONS.map((option) => ({
            kind: option.value,
            label: option.label,
            rune: option.rune,
            summary: option.summary,
            detail: option.detail,
            registerable: true,
            filterable: true,
          })),
    [catalogEntries],
  );
  const fallbackRegisterKind = registerOptions[0]?.kind ?? 'volundr';
  const effectiveWizard = (() => {
    const visibility =
      wizard.visibility === 'user' && canCreateTenantScope ? 'tenant' : wizard.visibility;
    const kind = registerOptions.some((option) => option.kind === wizard.kind)
      ? wizard.kind
      : fallbackRegisterKind;
    return visibility === wizard.visibility && kind === wizard.kind
      ? wizard
      : { ...wizard, visibility, kind };
  })();

  useEffect(() => {
    const handleOpenRegister = () => {
      setManualRegisterOpen(true);
      const params = new URLSearchParams(window.location.search);
      params.set('register', '1');
      const query = params.toString();
      window.history.replaceState({}, '', `${window.location.pathname}${query ? `?${query}` : ''}`);
    };
    window.addEventListener('guild:open-register', handleOpenRegister);
    return () => window.removeEventListener('guild:open-register', handleOpenRegister);
  }, []);

  const countsByKind = useMemo(() => {
    const counts: Record<'all' | InstanceKind, number> = {
      all: instances.length,
      volundr: 0,
      ting: 0,
      mimir: 0,
      bifrost: 0,
      ravn: 0,
      observatory: 0,
      generic: 0,
    };
    for (const instance of instances) {
      counts[instance.kind] += 1;
    }
    return counts;
  }, [instances]);

  const filteredInstances = useMemo(() => {
    const query = search.trim().toLowerCase();
    return instances.filter((instance) => {
      if (kindFilter !== 'all' && instance.kind !== kindFilter) return false;
      if (!query) return true;
      const haystack = [
        instance.name,
        instance.slug,
        instance.baseUrl,
        instance.visibility,
        scopeSummary(instance),
        inferAuth(instance),
        ...inferCapabilities(instance),
      ]
        .join(' ')
        .toLowerCase();
      return haystack.includes(query);
    });
  }, [instances, kindFilter, search]);

  const selectedInstance =
    filteredInstances.find((instance) => instance.id === selectedId) ??
    filteredInstances[0] ??
    null;
  const selectedHealth = selectedInstance ? (healthById[selectedInstance.id] ?? null) : null;
  const selectedHistory = selectedInstance ? (healthHistory[selectedInstance.id] ?? []) : [];

  useEffect(() => {
    if (!selectedInstance || healthMutation.isPending) return;
    if (healthById[selectedInstance.id]) return;
    healthMutation.mutate(selectedInstance.id);
  }, [healthById, healthMutation, selectedInstance]);

  if (!client) {
    return (
      <div className="niuu-p-6">
        <p className="niuu-text-sm niuu-text-text-secondary">
          The shared Niuu API is not configured, so Guild targets are unavailable.
        </p>
      </div>
    );
  }

  const accessGroups = {
    personal: instances.filter((instance) => instance.visibility === 'user'),
    tenant: instances.filter((instance) => instance.visibility === 'tenant'),
    system: instances.filter((instance) => instance.visibility === 'system'),
  };

  const connectionGroups = countByAuth(instances);

  return (
    <div className="niuu-min-h-full niuu-bg-bg-primary niuu-p-6" data-testid="guild-page">
      <div className="niuu-space-y-6">
        {section === 'instances' ? (
          <div className="niuu-space-y-5">
            <section className="niuu-rounded-[22px] niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-p-4">
              <div className="niuu-flex niuu-flex-wrap niuu-items-center niuu-gap-3">
                <label className="niuu-flex niuu-min-w-[260px] niuu-flex-1 niuu-items-center niuu-gap-2 niuu-rounded-xl niuu-border niuu-border-border-subtle niuu-bg-bg-tertiary niuu-px-3 niuu-py-2.5 focus-within:niuu-border-brand/40">
                  <Search className="niuu-h-4 niuu-w-4 niuu-text-text-muted" />
                  <input
                    type="search"
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                    placeholder="filter by name, endpoint, tag, or auth…"
                    className="niuu-min-w-0 niuu-flex-1 niuu-bg-transparent niuu-text-[13px] niuu-text-text-primary placeholder:niuu-text-text-muted focus:niuu-outline-none"
                  />
                </label>

                <div className="niuu-flex niuu-flex-wrap niuu-items-center niuu-gap-2">
                  {[
                    { value: 'all', label: 'all', rune: 'ᚹ' } as const,
                    ...filterOptions.map((option) => ({
                      value: option.kind,
                      label: option.label,
                      rune: option.rune,
                    })),
                  ].map((option) => {
                    const active = kindFilter === option.value;
                    const count = countsByKind[option.value as 'all' | InstanceKind];
                    return (
                      <button
                        key={option.value}
                        type="button"
                        onClick={() => setKindFilter(option.value)}
                        className={cn(
                          'niuu-inline-flex niuu-items-center niuu-gap-2 niuu-rounded-full niuu-border niuu-px-3 niuu-py-1.5 niuu-font-mono niuu-text-[11px] niuu-transition-colors',
                          active
                            ? 'niuu-border-brand/35 niuu-bg-brand/12 niuu-text-brand'
                            : 'niuu-border-border-subtle niuu-bg-bg-tertiary niuu-text-text-muted hover:niuu-text-text-primary',
                        )}
                      >
                        <Rune glyph={option.rune} size={13} className="niuu-text-current" />
                        <span>{option.label}</span>
                        <span className="niuu-text-[10px] niuu-text-text-faint">{count}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            </section>

            {instancesQuery.isLoading ? <LoadingState label="Loading registry…" /> : null}
            {instancesQuery.error ? (
              <ErrorState
                title="Failed to load registry"
                message={
                  instancesQuery.error instanceof Error
                    ? instancesQuery.error.message
                    : 'Failed to load visible instances.'
                }
              />
            ) : null}

            {!instancesQuery.isLoading && !instancesQuery.error ? (
              <section
                className={cn(
                  'niuu-grid niuu-gap-5',
                  detailOpen ? 'xl:niuu-grid-cols-[minmax(0,1.55fr)_360px]' : 'niuu-grid-cols-1',
                )}
              >
                <div className="niuu-space-y-4">
                  {filteredInstances.length === 0 ? (
                    <div className="niuu-rounded-[22px] niuu-border niuu-border-dashed niuu-border-border-subtle niuu-bg-bg-secondary niuu-p-8 niuu-text-sm niuu-text-text-secondary">
                      No instances match the current filters.
                    </div>
                  ) : (
                    <div className="niuu-grid niuu-gap-4 lg:niuu-grid-cols-2 2xl:niuu-grid-cols-3">
                      {filteredInstances.map((instance) => (
                        <InstanceCard
                          key={instance.id}
                          instance={instance}
                          selected={instance.id === selectedInstance?.id}
                          health={healthById[instance.id]}
                          onClick={() => {
                            setSelectedId(instance.id);
                            if (!detailOpen) setDetailOpen(true);
                          }}
                        />
                      ))}
                    </div>
                  )}
                </div>

                {detailOpen && selectedInstance ? (
                  <GuildDetailRail
                    instance={selectedInstance}
                    health={selectedHealth}
                    history={selectedHistory}
                    onTest={() => healthMutation.mutate(selectedInstance.id)}
                    onRefresh={() => void instancesQuery.refetch()}
                    onCollapse={() => setDetailOpen(false)}
                    isTesting={healthMutation.isPending}
                  />
                ) : null}
              </section>
            ) : null}
          </div>
        ) : null}

        {section === 'access' ? (
          <section className="niuu-space-y-5">
            <div className="niuu-grid niuu-gap-4 lg:niuu-grid-cols-3">
              {[
                {
                  title: 'Personal',
                  count: accessGroups.personal.length,
                  body: 'User-scoped instances are only visible to the owning user.',
                },
                {
                  title: 'Tenant',
                  count: accessGroups.tenant.length,
                  body: 'Tenant-scoped instances stay inside their tenant boundary.',
                },
                {
                  title: 'System',
                  count: accessGroups.system.length,
                  body: 'System-scoped instances are shared and admin-managed.',
                },
              ].map((card) => (
                <div
                  key={card.title}
                  className="niuu-rounded-[16px] niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-p-4"
                >
                  <div className="niuu-flex niuu-items-start niuu-justify-between niuu-gap-3">
                    <div>
                      <div className="niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.12em] niuu-text-text-faint">
                        visibility
                      </div>
                      <h2 className="niuu-mt-2 niuu-text-[15px] niuu-font-semibold niuu-text-text-primary">
                        {card.title}
                      </h2>
                    </div>
                    <span className="niuu-rounded-full niuu-bg-bg-elevated niuu-px-2 niuu-py-1 niuu-font-mono niuu-text-[10px] niuu-text-text-secondary">
                      {card.count}
                    </span>
                  </div>
                  <p className="niuu-mt-2 niuu-text-[12px] niuu-leading-5 niuu-text-text-secondary">
                    {card.body}
                  </p>
                </div>
              ))}
            </div>

            <div className="niuu-rounded-[18px] niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-p-4">
              <div className="niuu-flex niuu-items-start niuu-justify-between niuu-gap-4">
                <div>
                  <div className="niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.12em] niuu-text-text-faint">
                    current visibility
                  </div>
                  <h2 className="niuu-mt-2 niuu-text-[15px] niuu-font-semibold niuu-text-text-primary">
                    Visible registry entries
                  </h2>
                  <p className="niuu-mt-1 niuu-text-[12px] niuu-leading-5 niuu-text-text-secondary">
                    What the current identity can actually discover right now.
                  </p>
                </div>
                <span className="niuu-rounded-full niuu-bg-bg-elevated niuu-px-2 niuu-py-1 niuu-font-mono niuu-text-[10px] niuu-text-text-secondary">
                  {instances.length}
                </span>
              </div>
              <div className="niuu-mt-4 niuu-grid niuu-gap-4 md:niuu-grid-cols-2 xl:niuu-grid-cols-3">
                {instances.map((instance) => {
                  const badge = scopeBadge(instance);
                  const ScopeIcon = badge.icon;
                  return (
                    <div
                      key={`access-${instance.id}`}
                      className="niuu-flex niuu-flex-col niuu-gap-3 niuu-rounded-[16px] niuu-border niuu-border-border-subtle niuu-bg-bg-tertiary niuu-p-4"
                    >
                      <div className="niuu-flex niuu-items-start niuu-justify-between niuu-gap-3">
                        <div className="niuu-min-w-0">
                          <div className="niuu-truncate niuu-text-[14px] niuu-font-semibold niuu-text-text-primary">
                            {instance.name}
                          </div>
                          <div className="niuu-mt-1 niuu-truncate niuu-font-mono niuu-text-[10px] niuu-text-text-muted">
                            {kindMeta(instance.kind).label} · {instance.slug}
                          </div>
                        </div>
                        <span
                          className={cn(
                            'niuu-inline-flex niuu-items-center niuu-gap-1.5 niuu-rounded-full niuu-border niuu-px-2 niuu-py-1 niuu-font-mono niuu-text-[9px] niuu-uppercase niuu-tracking-[0.12em]',
                            badge.tone,
                          )}
                        >
                          <ScopeIcon className="niuu-h-3 niuu-w-3" />
                          {badge.label}
                        </span>
                      </div>
                      <div className="niuu-space-y-2">
                        <div>
                          <div className="niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.12em] niuu-text-text-faint">
                            endpoint
                          </div>
                          <div className="niuu-mt-1 niuu-truncate niuu-font-mono niuu-text-[11px] niuu-text-text-secondary">
                            {instance.baseUrl}
                          </div>
                        </div>
                        <div className="niuu-grid niuu-grid-cols-2 niuu-gap-3">
                          <div>
                            <div className="niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.12em] niuu-text-text-faint">
                              boundary
                            </div>
                            <div className="niuu-mt-1 niuu-text-[12px] niuu-text-text-secondary">
                              {scopeSummary(instance)}
                            </div>
                          </div>
                          <div>
                            <div className="niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.12em] niuu-text-text-faint">
                              auth
                            </div>
                            <div className="niuu-mt-1 niuu-text-[12px] niuu-text-text-secondary">
                              {inferAuth(instance)}
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </section>
        ) : null}

        {section === 'connections' ? (
          <section className="niuu-space-y-5">
            <div className="niuu-grid niuu-gap-4 lg:niuu-grid-cols-3">
              <div className="niuu-rounded-[20px] niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-p-5">
                <h2 className="niuu-text-[16px] niuu-font-semibold niuu-text-text-primary">
                  Endpoint health
                </h2>
                <p className="niuu-mt-2 niuu-text-[13px] niuu-leading-6 niuu-text-text-secondary">
                  Live probes hang off the instance record so Guild can validate remote backends
                  before Ting or Volundr targets them.
                </p>
              </div>
              <div className="niuu-rounded-[20px] niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-p-5">
                <h2 className="niuu-text-[16px] niuu-font-semibold niuu-text-text-primary">
                  Credential attachment
                </h2>
                <p className="niuu-mt-2 niuu-text-[13px] niuu-leading-6 niuu-text-text-secondary">
                  Registration now binds endpoints to personal or tenant credentials without moving
                  secret values into the registry itself.
                </p>
              </div>
              <div className="niuu-rounded-[20px] niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-p-5">
                <h2 className="niuu-text-[16px] niuu-font-semibold niuu-text-text-primary">
                  Future connection policy
                </h2>
                <p className="niuu-mt-2 niuu-text-[13px] niuu-leading-6 niuu-text-text-secondary">
                  This is where rotation state, policy checks, and transport defaults can grow next.
                </p>
              </div>
            </div>

            <div className="niuu-rounded-[22px] niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-p-5">
              <div className="niuu-flex niuu-items-center niuu-justify-between">
                <div>
                  <h2 className="niuu-text-[16px] niuu-font-semibold niuu-text-text-primary">
                    Auth surface
                  </h2>
                  <p className="niuu-text-[13px] niuu-text-text-secondary">
                    Top auth patterns currently used by visible instances.
                  </p>
                </div>
              </div>
              <div className="niuu-mt-4 niuu-grid niuu-gap-3 lg:niuu-grid-cols-2">
                {connectionGroups.map((group) => (
                  <div
                    key={group.label}
                    className="niuu-flex niuu-items-center niuu-justify-between niuu-rounded-xl niuu-bg-bg-elevated niuu-p-3"
                  >
                    <span className="niuu-text-[13px] niuu-text-text-primary">{group.label}</span>
                    <span className="niuu-rounded-full niuu-bg-bg-secondary niuu-px-2 niuu-py-1 niuu-font-mono niuu-text-[11px] niuu-text-text-secondary">
                      {group.count}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </section>
        ) : null}
      </div>

      <RegisterWizard
        key={registerOpen ? 'register-open' : 'register-closed'}
        open={registerOpen}
        onOpenChange={(open) => {
          setManualRegisterOpen(open);
          const params = new URLSearchParams(window.location.search);
          if (open) {
            params.set('register', '1');
          } else {
            params.delete('register');
          }
          const query = params.toString();
          window.history.replaceState(
            {},
            '',
            `${window.location.pathname}${query ? `?${query}` : ''}`,
          );
          if (!open) resetWizard();
        }}
        registerOptions={registerOptions.map((option) => ({
          value: option.kind,
          label: option.label,
          rune: option.rune,
          summary: option.summary,
          detail: option.detail,
        }))}
        currentIdentity={currentIdentity}
        canCreateSystemScope={canCreateSystemScope}
        canCreateTenantScope={canCreateTenantScope}
        wizard={effectiveWizard}
        setWizard={setWizard}
        userCredentials={userCredentialsQuery.data ?? []}
        tenantCredentials={tenantCredentialsQuery.data ?? []}
        createMutationPending={createMutation.isPending}
        createError={createMutation.error}
        onSubmit={() => createMutation.mutate()}
      />
    </div>
  );
}
