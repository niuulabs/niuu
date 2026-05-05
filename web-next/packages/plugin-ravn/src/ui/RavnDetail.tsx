import { useMemo, useState, type ReactNode } from 'react';
import { LiveBadge, MountChip, PersonaAvatar, StateDot, relTime } from '@niuulabs/ui';
import type { BudgetState } from '@niuulabs/domain';
import type { Ravn } from '../domain/ravn';
import type { Message, MessageKind } from '../domain/message';
import type { Session } from '../domain/session';
import type { Trigger } from '../domain/trigger';
import { useTriggers } from './hooks/useTriggers';
import { useSessions, useRavnActivity } from './hooks/useSessions';
import { useRavnBudget } from './hooks/useBudget';
import { ravnStatusToDotState } from './grouping';
import { loadStorage, saveStorage } from './storage';
import './RavnDetail.css';

const TAB_STORAGE_KEY = 'ravn.detail.tab';

type TabId = 'overview' | 'triggers' | 'activity' | 'sessions' | 'connectivity';

const KIND_FILTER_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'all', label: 'all' },
  { value: 'user', label: 'user' },
  { value: 'asst', label: 'asst' },
  { value: 'tool', label: 'tool' },
  { value: 'emit', label: 'emit' },
  { value: 'think', label: 'think' },
  { value: 'system', label: 'system' },
];

function formatTs(isoTs: string): string {
  return new Date(isoTs).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function normalizeLabel(value: string): string {
  return value.replace(/_/g, ' ').replace(/-/g, ' ');
}

function kindMatchesFilter(kind: MessageKind, filter: string): boolean {
  if (filter === 'all') return true;
  if (filter === 'tool') return kind === 'tool_call' || kind === 'tool_result';
  return kind === filter;
}

function pillStateLabel(status: Ravn['status']): string {
  return normalizeLabel(status);
}

function detailSubtitle(ravn: Ravn): string {
  return [
    ravn.role ? normalizeLabel(ravn.role) : null,
    ravn.location ? normalizeLabel(ravn.location) : null,
    ravn.deployment ? normalizeLabel(ravn.deployment) : null,
  ]
    .filter(Boolean)
    .join(' · ');
}

function buildSpecialisations(ravn: Ravn): string {
  const values = [
    ravn.role ? normalizeLabel(ravn.role) : null,
    ravn.writeRouting ? `${normalizeLabel(ravn.writeRouting)} routing` : null,
    ...(ravn.mounts ?? []).slice(0, 2).map((mount) => normalizeLabel(mount.name)),
  ].filter(Boolean);

  return values.length > 0 ? values.join(', ') : '—';
}

function spendPercent(budget?: BudgetState): number {
  if (!budget || budget.capUsd <= 0) return 0;
  return Math.round((budget.spentUsd / budget.capUsd) * 100);
}

function dispatchSessionSelection(sessionId: string) {
  window.dispatchEvent(new CustomEvent('ravn:session-selected', { detail: { sessionId } }));
}

interface KeyValueRowProps {
  label: string;
  value: ReactNode;
}

function KeyValueRow({ label, value }: KeyValueRowProps) {
  return (
    <div className="rv-kv-row">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

interface OverviewSectionProps {
  ravn: Ravn;
  budget?: BudgetState;
  sessions: Session[];
}

function OverviewSection({ ravn, budget, sessions }: OverviewSectionProps) {
  const openSessions = sessions.filter((session) => session.status === 'running').length;
  const totalSessions = sessions.length;
  const percentage = spendPercent(budget);

  return (
    <div className="rv-detail-overview" data-testid="section-body-overview">
      <section className="rv-panel" data-testid="identity-panel">
        <header className="rv-panel__head">
          <h3>Identity</h3>
        </header>
        <div className="rv-panel__body">
          <dl className="rv-kv-list">
            <KeyValueRow label="id" value={<span className="rv-value-mono">{ravn.id}</span>} />
            <KeyValueRow
              label="persona"
              value={<span className="rv-value-strong">{ravn.personaName}</span>}
            />
            <KeyValueRow
              label="role"
              value={<span className="rv-value-mono">{ravn.role ?? '—'}</span>}
            />
            <KeyValueRow
              label="specialisations"
              value={<span className="rv-value-mono">{buildSpecialisations(ravn)}</span>}
            />
            {ravn.summary && (
              <KeyValueRow
                label="summary"
                value={<span className="rv-value-copy">{ravn.summary}</span>}
              />
            )}
          </dl>
        </div>
      </section>

      <section className="rv-panel" data-testid="runtime-panel">
        <header className="rv-panel__head">
          <h3>Runtime</h3>
        </header>
        <div className="rv-panel__body">
          <dl className="rv-kv-list">
            <KeyValueRow
              label="state"
              value={
                <span className="rv-state-pill">
                  <StateDot
                    state={ravnStatusToDotState(ravn.status)}
                    pulse={ravn.status === 'active'}
                    size={8}
                  />
                  {pillStateLabel(ravn.status)}
                </span>
              }
            />
            <KeyValueRow
              label="cascade"
              value={<span className="rv-value-mono">{ravn.cascade ?? '—'}</span>}
            />
            <KeyValueRow
              label="routing"
              value={<span className="rv-value-mono">{ravn.writeRouting ?? '—'}</span>}
            />
            <KeyValueRow
              label="model"
              value={<span className="rv-value-mono">{ravn.model}</span>}
            />
            <KeyValueRow
              label="last activity"
              value={
                <span className="rv-value-strong">{relTime(ravn.updatedAt ?? ravn.createdAt)}</span>
              }
            />
            <KeyValueRow
              label="sessions"
              value={
                <span className="rv-value-strong">
                  {openSessions} open / {totalSessions} total
                </span>
              }
            />
            <KeyValueRow
              label="today's spend"
              value={
                budget ? (
                  <span className="rv-spend-row">
                    <span className="rv-value-strong">${budget.spentUsd.toFixed(2)}</span>
                    <span className="rv-value-mono">of ${budget.capUsd.toFixed(2)}</span>
                    <span className="rv-percent-pill">{percentage}%</span>
                  </span>
                ) : (
                  <span className="rv-value-mono">—</span>
                )
              }
            />
          </dl>
        </div>
      </section>

      {ravn.mounts && ravn.mounts.length > 0 && (
        <section className="rv-panel rv-panel--wide" data-testid="mounts-panel">
          <header className="rv-panel__head">
            <h3>Mimir mounts</h3>
            <span className="rv-panel__count">{ravn.mounts.length} mounts</span>
          </header>
          <div className="rv-panel__body rv-panel__body--wide">
            <div className="rv-mounts-row">
              {ravn.mounts.map((mount) => (
                <MountChip key={mount.name} name={mount.name} role={mount.role} />
              ))}
            </div>

            <div className="rv-routing-block">
              <div className="rv-routing-title">write routing</div>
              <dl className="rv-routing-list">
                <KeyValueRow
                  label="mode"
                  value={<span className="rv-value-mono">{ravn.writeRouting ?? '—'}</span>}
                />
                <KeyValueRow
                  label="gateway"
                  value={
                    <span className="rv-value-mono">
                      {ravn.gatewayChannels?.join(', ') || 'none'}
                    </span>
                  }
                />
                <KeyValueRow
                  label="events"
                  value={
                    <span className="rv-value-mono">
                      {ravn.eventSubscriptions?.join(', ') || 'none'}
                    </span>
                  }
                />
              </dl>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}

interface TriggersSectionProps {
  triggers: Trigger[];
}

const TRIGGER_KIND_LABELS: Record<string, string> = {
  cron: '⏱',
  event: '⚡',
  webhook: '⇄',
  manual: '▶',
};

function TriggersSection({ triggers }: TriggersSectionProps) {
  return (
    <div className="rv-section-body" data-testid="triggers-section-body">
      {triggers.length === 0 ? (
        <p className="rv-empty-text">No triggers configured</p>
      ) : (
        <div className="rv-stack-list">
          {triggers.map((trigger) => (
            <div key={trigger.id} className="rv-stack-card" data-testid="trigger-card">
              <div className="rv-stack-card__main">
                <span
                  className={`rv-stack-kind rv-stack-kind--${trigger.kind}`}
                  data-testid="trigger-kind"
                >
                  <span aria-hidden="true">
                    {TRIGGER_KIND_LABELS[trigger.kind] ?? trigger.kind}
                  </span>
                  {trigger.kind}
                </span>
                <div className="rv-stack-card__copy">
                  <div className="rv-stack-card__title" data-testid="trigger-spec">
                    {trigger.spec}
                  </div>
                  <div className="rv-stack-card__meta">
                    {trigger.lastFiredAt && (
                      <span data-testid="trigger-last-fired">
                        last fired {relTime(trigger.lastFiredAt)}
                      </span>
                    )}
                    {trigger.fireCount != null && (
                      <span data-testid="trigger-fire-count">{trigger.fireCount} fires</span>
                    )}
                    {!trigger.enabled && <span>disabled</span>}
                  </div>
                </div>
              </div>

              <button
                type="button"
                className={`rv-toggle${trigger.enabled ? ' rv-toggle--on' : ''}`}
                aria-label={trigger.enabled ? 'Disable trigger' : 'Enable trigger'}
                aria-checked={trigger.enabled}
                data-testid="trigger-toggle"
              >
                <span className="rv-toggle__thumb" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

interface ActivitySectionProps {
  messages: Message[];
  isActive: boolean;
  isLoading: boolean;
}

function ActivitySection({ messages, isActive, isLoading }: ActivitySectionProps) {
  const [kindFilter, setKindFilter] = useState<string>('all');
  const filtered = messages.filter((message) => kindMatchesFilter(message.kind, kindFilter));
  const displayMessages = filtered.slice(-120);

  if (isLoading) {
    return <p className="rv-empty-text">Loading activity…</p>;
  }

  return (
    <div className="rv-section-body" data-testid="activity-section-body">
      <div className="rv-section-toolbar">
        {isActive && (
          <span data-testid="activity-live">
            <LiveBadge label="live" />
          </span>
        )}

        <div className="rv-activity-filter" data-testid="activity-filter">
          {KIND_FILTER_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => setKindFilter(option.value)}
              className={`rv-activity-filter-btn${kindFilter === option.value ? ' rv-activity-filter-btn--active' : ''}`}
              data-testid={`activity-filter-${option.value}`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {displayMessages.length === 0 ? (
        <p className="rv-empty-text">
          {messages.length === 0 ? 'No activity for this ravn' : 'No messages match this filter'}
        </p>
      ) : (
        <div className="rv-log-list" data-testid="activity-messages">
          {displayMessages.map((message) => (
            <div key={message.id} className="rv-log-row" data-testid="activity-message">
              <span className="rv-log-row__ts">{formatTs(message.ts)}</span>
              <span
                className={`rv-log-kind rv-log-kind--${message.kind}`}
                data-testid="activity-kind-badge"
              >
                {message.kind}
              </span>
              <span className="rv-log-row__body">
                {message.content.slice(0, 160)}
                {message.content.length > 160 ? '…' : ''}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

interface SessionsSectionProps {
  sessions: Session[];
}

function SessionsSection({ sessions }: SessionsSectionProps) {
  return (
    <div className="rv-section-body" data-testid="sessions-section-body">
      {sessions.length === 0 ? (
        <p className="rv-empty-text">No sessions</p>
      ) : (
        <div className="rv-stack-list">
          {sessions.map((session) => (
            <button
              key={session.id}
              type="button"
              onClick={() => dispatchSessionSelection(session.id)}
              className="rv-stack-card rv-stack-card--button"
              data-testid="session-card"
            >
              <div className="rv-stack-card__main">
                <span className="rv-stack-session-dot">
                  <StateDot
                    state={
                      session.status === 'running'
                        ? 'running'
                        : session.status === 'failed'
                          ? 'failed'
                          : 'unknown'
                    }
                    pulse={session.status === 'running'}
                    size={8}
                  />
                </span>
                <div className="rv-stack-card__copy">
                  <div className="rv-stack-card__title">
                    {session.title ?? session.id.slice(0, 8)}
                  </div>
                  <div className="rv-stack-card__meta">
                    <span className="rv-value-mono">{session.status}</span>
                    <span className="rv-value-mono">{relTime(session.createdAt)}</span>
                    <span className="rv-value-mono">{session.model}</span>
                  </div>
                </div>
              </div>

              <div className="rv-stack-card__metrics">
                {session.messageCount != null && (
                  <span data-testid="session-message-count">{session.messageCount} msgs</span>
                )}
                {session.costUsd != null && (
                  <span data-testid="session-cost">${session.costUsd.toFixed(2)}</span>
                )}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

interface ConnectivitySectionProps {
  ravn: Ravn;
}

function ConnectivitySection({ ravn }: ConnectivitySectionProps) {
  const mcpServers = ravn.mcpServers ?? [];
  const gatewayChannels = ravn.gatewayChannels ?? [];
  const eventSubscriptions = ravn.eventSubscriptions ?? [];

  return (
    <div className="rv-detail-connectivity" data-testid="connectivity-section-body">
      <section className="rv-panel" data-testid="conn-mcp-panel">
        <header className="rv-panel__head">
          <h3>MCP servers</h3>
          <span className="rv-panel__count">{mcpServers.length}</span>
        </header>
        <div className="rv-panel__body">
          {mcpServers.length === 0 ? (
            <span className="rv-empty-text">None configured</span>
          ) : (
            <div className="rv-chip-row">
              {mcpServers.map((server) => (
                <span
                  key={server}
                  className="rv-conn-chip rv-conn-chip--mcp"
                  data-testid="mcp-server-chip"
                >
                  {server}
                </span>
              ))}
            </div>
          )}
        </div>
      </section>

      <section className="rv-panel" data-testid="conn-gateway-panel">
        <header className="rv-panel__head">
          <h3>Gateway channels</h3>
          <span className="rv-panel__count">{gatewayChannels.length}</span>
        </header>
        <div className="rv-panel__body">
          {gatewayChannels.length === 0 ? (
            <span className="rv-empty-text">None configured</span>
          ) : (
            <div className="rv-chip-row">
              {gatewayChannels.map((channel) => (
                <span
                  key={channel}
                  className="rv-conn-chip rv-conn-chip--gateway"
                  data-testid="gateway-channel-chip"
                >
                  {channel}
                </span>
              ))}
            </div>
          )}
        </div>
      </section>

      <section className="rv-panel rv-panel--wide" data-testid="conn-events-panel">
        <header className="rv-panel__head">
          <h3>Event subscriptions</h3>
          <span className="rv-panel__count">{eventSubscriptions.length}</span>
        </header>
        <div className="rv-panel__body">
          {eventSubscriptions.length === 0 ? (
            <span className="rv-empty-text">None configured</span>
          ) : (
            <div className="rv-chip-row">
              {eventSubscriptions.map((event) => (
                <span
                  key={event}
                  className="rv-conn-chip rv-conn-chip--event"
                  data-testid="event-subscription-chip"
                >
                  {event}
                </span>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

export interface RavnDetailProps {
  ravn: Ravn;
  onClose?: () => void;
}

export function RavnDetail({ ravn, onClose }: RavnDetailProps) {
  const [activeTab, setActiveTab] = useState<TabId>(() =>
    loadStorage<TabId>(TAB_STORAGE_KEY, 'overview'),
  );

  const { data: budget } = useRavnBudget(ravn.id);
  const { data: triggers } = useTriggers();
  const { data: sessions } = useSessions();
  const { data: activityMessages, isLoading: activityLoading } = useRavnActivity(ravn.id);

  const ravnTriggers = useMemo(
    () => (triggers ?? []).filter((trigger) => trigger.personaName === ravn.personaName),
    [triggers, ravn.personaName],
  );
  const ravnSessions = useMemo(
    () => (sessions ?? []).filter((session) => session.ravnId === ravn.id),
    [sessions, ravn.id],
  );

  const openSessionId = ravnSessions.find((session) => session.status === 'running')?.id;

  const tabs: Array<{ id: TabId; label: string; count?: number }> = [
    { id: 'overview', label: 'Overview' },
    { id: 'triggers', label: 'Triggers', count: ravnTriggers.length },
    { id: 'activity', label: 'Activity', count: activityMessages.length },
    { id: 'sessions', label: 'Sessions', count: ravnSessions.length },
    { id: 'connectivity', label: 'Connectivity' },
  ];

  const subtitle = detailSubtitle(ravn);

  return (
    <div className="rv-detail" data-testid="ravn-detail">
      <header className="rv-detail__hero">
        <div className="rv-detail__hero-left">
          <PersonaAvatar
            role={ravn.role ?? 'build'}
            letter={ravn.letter ?? ravn.personaName.charAt(0).toUpperCase()}
            size={46}
          />
          <div>
            <div className="rv-detail__title-wrap">
              <h1 className="rv-detail__title">{ravn.personaName}</h1>
            </div>
            {subtitle && <p className="rv-detail__subtitle">{subtitle}</p>}
          </div>
        </div>

        <div className="rv-detail__hero-actions">
          <span className="rv-state-pill">
            <StateDot
              state={ravnStatusToDotState(ravn.status)}
              pulse={ravn.status === 'active'}
              size={8}
            />
            {pillStateLabel(ravn.status)}
          </span>

          <button type="button" className="rv-action-btn">
            pause
          </button>

          <button
            type="button"
            className="rv-action-btn rv-action-btn--primary"
            onClick={() => {
              if (openSessionId) dispatchSessionSelection(openSessionId);
            }}
          >
            open session
          </button>

          {onClose && (
            <button
              type="button"
              onClick={onClose}
              className="rv-detail__close"
              data-testid="detail-close-btn"
              aria-label="Close detail pane"
            >
              ✕
            </button>
          )}
        </div>
      </header>

      <nav className="rv-sectabs" aria-label="Ravn detail sections" data-testid="ravn-sectabs">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={activeTab === tab.id}
            onClick={() => {
              saveStorage(TAB_STORAGE_KEY, tab.id);
              setActiveTab(tab.id);
            }}
            className={`rv-sectab${activeTab === tab.id ? ' rv-sectab--active' : ''}`}
            data-testid={`sectab-${tab.id}`}
          >
            {tab.label}
            {tab.count != null && tab.count > 0 && (
              <span className="rv-sectabs-n">{tab.count}</span>
            )}
          </button>
        ))}
      </nav>

      <div className="rv-detail__content">
        {activeTab === 'overview' && (
          <OverviewSection ravn={ravn} budget={budget} sessions={ravnSessions} />
        )}
        {activeTab === 'triggers' && <TriggersSection triggers={ravnTriggers} />}
        {activeTab === 'activity' && (
          <ActivitySection
            messages={activityMessages}
            isActive={ravn.status === 'active'}
            isLoading={activityLoading}
          />
        )}
        {activeTab === 'sessions' && <SessionsSection sessions={ravnSessions} />}
        {activeTab === 'connectivity' && <ConnectivitySection ravn={ravn} />}
      </div>
    </div>
  );
}
