import { useMemo, useState, type ReactNode } from 'react';
import {
  Dialog,
  DialogContent,
  LiveBadge,
  MountChip,
  PersonaAvatar,
  StateDot,
  relTime,
} from '@niuulabs/ui';
import { FileText, MessageSquare, Pause, Play, Plus, RotateCw, Trash2, X } from 'lucide-react';
import type { BudgetState } from '@niuulabs/domain';
import type { Ravn } from '../domain/ravn';
import type { Message, MessageKind } from '../domain/message';
import type { Session } from '../domain/session';
import type { Trigger } from '../domain/trigger';
import { useTriggers } from './hooks/useTriggers';
import { useSessions, useRavnActivity } from './hooks/useSessions';
import { useRavnBudget } from './hooks/useBudget';
import {
  useCreateResidentSession,
  useDeleteResident,
  useDeleteResidentSession,
  useResidentLifecycle,
  useResidentLogs,
  useResidentProfiles,
  useResidentSessions,
} from './hooks/useResidentControl';
import { ravnStatusToDotState } from './grouping';
import { ResidentModelSelect } from './ResidentModelSelect';
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
    ravn.instanceName
      ? normalizeLabel(ravn.instanceName)
      : ravn.location
        ? normalizeLabel(ravn.location)
        : null,
    ravn.engine
      ? normalizeLabel(ravn.engine)
      : ravn.deployment
        ? normalizeLabel(ravn.deployment)
        : null,
  ]
    .filter(Boolean)
    .join(' · ');
}

function nameForRavn(ravn: Ravn): string {
  return ravn.residentName || ravn.personaName || ravn.id.slice(0, 8);
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

function sessionKey(session: Pick<Session, 'id' | 'ravnId' | 'instanceId'>): string {
  return session.instanceId
    ? `${encodeURIComponent(session.instanceId)}:${encodeURIComponent(session.ravnId)}:${session.id}`
    : session.id;
}

function dispatchSessionSelection(session: Session) {
  saveStorage('ravn.session', sessionKey(session));
  window.dispatchEvent(
    new CustomEvent('ravn:session-selected', {
      detail: { sessionId: session.id, ravnId: session.ravnId, instanceId: session.instanceId },
    }),
  );
  const params = new URLSearchParams(window.location.search);
  params.set('session', session.id);
  params.set('ravn_id', session.ravnId);
  if (session.instanceId) params.set('instance_id', session.instanceId);
  else params.delete('instance_id');
  window.history.pushState(null, '', `/ravn/sessions?${params.toString()}`);
  window.dispatchEvent(new Event('popstate'));
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
              value={<span className="rv-value-strong">{ravn.personaName || '—'}</span>}
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
            {ravn.managed && (
              <>
                <KeyValueRow
                  label="backend"
                  value={<span className="rv-value-mono">{ravn.backend ?? '—'}</span>}
                />
                <KeyValueRow
                  label="engine"
                  value={<span className="rv-value-mono">{ravn.engine ?? '—'}</span>}
                />
                <KeyValueRow
                  label="profile"
                  value={<span className="rv-value-mono">{ravn.profileId ?? '—'}</span>}
                />
                <KeyValueRow
                  label="target"
                  value={
                    <span className="rv-value-mono">
                      {ravn.instanceName ?? ravn.instanceSlug ?? ravn.instanceId ?? '—'}
                    </span>
                  }
                />
                <KeyValueRow
                  label="desired"
                  value={<span className="rv-value-mono">{ravn.desiredState ?? '—'}</span>}
                />
                <KeyValueRow
                  label="observed"
                  value={<span className="rv-value-mono">{ravn.observedState ?? '—'}</span>}
                />
              </>
            )}
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

      {ravn.managed && (
        <section className="rv-panel rv-panel--wide" data-testid="resident-contract-panel">
          <header className="rv-panel__head">
            <h3>Runtime contract</h3>
            <span className="rv-panel__count">{ravn.capabilities?.length ?? 0} capabilities</span>
          </header>
          <div className="rv-panel__body rv-resident-contract">
            <div className="rv-chip-row">
              {(ravn.capabilities ?? []).map((capability) => (
                <span key={capability} className="rv-conn-chip" data-testid="resident-capability">
                  {capability}
                </span>
              ))}
            </div>
            {(ravn.conditions ?? []).length > 0 && (
              <div className="rv-condition-list" data-testid="resident-conditions">
                {(ravn.conditions ?? []).map((condition) => (
                  <div key={condition.type} className="rv-condition-row">
                    <span
                      className={`rv-condition-status rv-condition-status--${condition.status}`}
                    >
                      {condition.status}
                    </span>
                    <strong>{condition.type}</strong>
                    <span>{condition.reason || condition.message || 'No detail reported'}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>
      )}

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
  ravn: Ravn;
  sessions: Session[];
}

function SessionsSection({ ravn, sessions }: SessionsSectionProps) {
  const [createOpen, setCreateOpen] = useState(false);
  const [title, setTitle] = useState('');
  const [model, setModel] = useState(ravn.model);
  const [pendingDelete, setPendingDelete] = useState<Session | null>(null);
  const residentActive = ravn.observedState === 'active';
  const canList = Boolean(
    ravn.managed && residentActive && ravn.capabilities?.includes('session.list'),
  );
  const canCreate = Boolean(
    ravn.managed && residentActive && ravn.capabilities?.includes('session.create'),
  );
  const canDelete = Boolean(
    ravn.managed && residentActive && ravn.capabilities?.includes('session.delete'),
  );
  const residentSessions = useResidentSessions(ravn, canList);
  const profiles = useResidentProfiles(canCreate);
  const createSession = useCreateResidentSession(ravn);
  const deleteSession = useDeleteResidentSession(ravn);
  const visibleSessions = canList ? (residentSessions.data ?? []) : sessions;
  const profile = profiles.data?.find(
    (candidate) => candidate.id === ravn.profileId && candidate.instanceId === ravn.instanceId,
  );
  const allowedModels = profile?.allowedModels ?? [];

  async function create() {
    if (!title.trim()) return;
    const selectedModel = allowedModels.includes(model) ? model : (profile?.defaultModel ?? '');
    let session: Session;
    try {
      session = await createSession.mutateAsync({
        title: title.trim(),
        ...(selectedModel && { model: selectedModel }),
      });
    } catch {
      return;
    }
    setCreateOpen(false);
    setTitle('');
    dispatchSessionSelection(session);
  }

  async function remove() {
    if (!pendingDelete) return;
    try {
      await deleteSession.mutateAsync(pendingDelete.id);
    } catch {
      return;
    }
    setPendingDelete(null);
  }

  return (
    <div className="rv-section-body" data-testid="sessions-section-body">
      {canCreate && (
        <div className="rv-section-toolbar rv-section-toolbar--end">
          <button
            type="button"
            className="rv-action-btn"
            onClick={() => setCreateOpen(true)}
            data-testid="resident-session-create-open"
          >
            <Plus size={14} aria-hidden="true" />
            New conversation
          </button>
        </div>
      )}

      {residentSessions.isError && (
        <p className="rv-inline-error" role="alert">
          {residentSessions.error instanceof Error
            ? residentSessions.error.message
            : 'Failed to load resident sessions'}
        </p>
      )}

      {canList && residentSessions.isLoading && (
        <p className="rv-empty-text">Loading conversations…</p>
      )}

      {!residentSessions.isLoading && visibleSessions.length === 0 ? (
        <p className="rv-empty-text">No sessions</p>
      ) : visibleSessions.length > 0 ? (
        <div className="rv-stack-list">
          {visibleSessions.map((session) => (
            <div key={session.id} className="rv-stack-card">
              <button
                type="button"
                onClick={() => dispatchSessionSelection(session)}
                className="rv-stack-card__open"
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
              {canDelete && (
                <button
                  type="button"
                  className="rv-icon-btn rv-icon-btn--danger"
                  onClick={() => setPendingDelete(session)}
                  title="Close conversation"
                  aria-label={`Close ${session.title ?? 'conversation'}`}
                  data-testid="resident-session-delete-open"
                >
                  <X size={15} aria-hidden="true" />
                </button>
              )}
            </div>
          ))}
        </div>
      ) : null}

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent title="New conversation" className="rv-session-dialog">
          <div className="rv-deploy-form">
            <label className="rv-form-field">
              <span>Title</span>
              <input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                maxLength={255}
                data-testid="resident-session-title"
              />
            </label>
            {allowedModels.length > 0 && (
              <label className="rv-form-field">
                <span>Model</span>
                <ResidentModelSelect
                  allowedModels={allowedModels}
                  modelPrefix={profile?.modelPrefix ?? ''}
                  value={allowedModels.includes(model) ? model : (profile?.defaultModel ?? '')}
                  onChange={setModel}
                  testId="resident-session-model"
                />
              </label>
            )}
            {createSession.isError && (
              <div className="rv-form-error" role="alert">
                {createSession.error instanceof Error
                  ? createSession.error.message
                  : 'Conversation creation failed'}
              </div>
            )}
            <div className="rv-form-actions">
              <button type="button" className="rv-action-btn" onClick={() => setCreateOpen(false)}>
                Cancel
              </button>
              <button
                type="button"
                className="rv-action-btn rv-action-btn--primary"
                disabled={!title.trim() || createSession.isPending}
                onClick={() => void create()}
                data-testid="resident-session-create-submit"
              >
                {createSession.isPending ? 'Creating…' : 'Create'}
              </button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(pendingDelete)}
        onOpenChange={(open) => !open && setPendingDelete(null)}
      >
        <DialogContent
          title="Close conversation"
          description="This removes the conversation and its engine-owned history."
        >
          {deleteSession.isError && (
            <div className="rv-form-error" role="alert">
              {deleteSession.error instanceof Error
                ? deleteSession.error.message
                : 'Conversation deletion failed'}
            </div>
          )}
          <div className="rv-form-actions">
            <button type="button" className="rv-action-btn" onClick={() => setPendingDelete(null)}>
              Cancel
            </button>
            <button
              type="button"
              className="rv-action-btn rv-action-btn--danger"
              disabled={deleteSession.isPending}
              onClick={() => void remove()}
              data-testid="resident-session-delete-confirm"
            >
              {deleteSession.isPending ? 'Closing…' : 'Close'}
            </button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

interface ConnectivitySectionProps {
  ravn: Ravn;
}

function ConnectivitySection({ ravn }: ConnectivitySectionProps) {
  const [logsOpen, setLogsOpen] = useState(false);
  const mcpServers = ravn.mcpServers ?? [];
  const gatewayChannels = ravn.gatewayChannels ?? [];
  const eventSubscriptions = ravn.eventSubscriptions ?? [];
  const canReadLogs = Boolean(ravn.managed && ravn.capabilities?.includes('logs'));
  const logs = useResidentLogs(ravn, logsOpen && canReadLogs);
  const operationalEndpoints = (ravn.endpoints ?? []).filter(
    (endpoint) => endpoint.kind === 'metrics' && ravn.capabilities?.includes('metrics'),
  );

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

      {(canReadLogs || operationalEndpoints.length > 0) && (
        <section className="rv-panel rv-panel--wide" data-testid="resident-observability-panel">
          <header className="rv-panel__head">
            <h3>Observability</h3>
          </header>
          <div className="rv-panel__body rv-chip-row">
            {canReadLogs && (
              <button
                type="button"
                className="rv-conn-chip rv-conn-link"
                onClick={() => setLogsOpen(true)}
              >
                <FileText size={13} aria-hidden="true" />
                Logs
              </button>
            )}
            {operationalEndpoints.map((endpoint) => (
              <a
                key={`${endpoint.kind}:${endpoint.url}`}
                className="rv-conn-chip rv-conn-link"
                href={endpoint.url}
                target="_blank"
                rel="noreferrer"
              >
                {normalizeLabel(endpoint.kind)}
              </a>
            ))}
          </div>
        </section>
      )}

      <Dialog open={logsOpen} onOpenChange={setLogsOpen}>
        <DialogContent title="Resident logs" className="rv-logs-dialog">
          {logs.isLoading && <div className="rv-form-state">Loading logs…</div>}
          {logs.isError && (
            <div className="rv-form-error" role="alert">
              {logs.error instanceof Error ? logs.error.message : 'Failed to load resident logs'}
            </div>
          )}
          {logs.data && logs.data.entries.length === 0 && (
            <div className="rv-form-state">No log entries reported.</div>
          )}
          {logs.data && logs.data.entries.length > 0 && (
            <div className="rv-resident-logs" data-testid="resident-log-entries">
              {logs.data.entries.map((entry, index) => (
                <div
                  key={`${entry.timestampMs}:${entry.source}:${index}`}
                  className="rv-resident-log"
                >
                  <span>{new Date(entry.timestampMs).toLocaleTimeString()}</span>
                  <strong>{entry.level || 'info'}</strong>
                  <span>{entry.source}</span>
                  <p>{entry.message}</p>
                </div>
              ))}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

export interface RavnDetailProps {
  ravn: Ravn;
  onClose?: () => void;
  onDeleted?: () => void;
}

export function RavnDetail({ ravn, onClose, onDeleted }: RavnDetailProps) {
  const [activeTab, setActiveTab] = useState<TabId>(() =>
    loadStorage<TabId>(TAB_STORAGE_KEY, 'overview'),
  );
  const [deleteOpen, setDeleteOpen] = useState(false);

  const { data: budget } = useRavnBudget(ravn.id);
  const { data: triggers } = useTriggers();
  const { data: sessions } = useSessions();
  const { data: activityMessages, isLoading: activityLoading } = useRavnActivity(ravn.id);
  const lifecycle = useResidentLifecycle();
  const deleteResident = useDeleteResident();

  const ravnTriggers = useMemo(
    () => (triggers ?? []).filter((trigger) => trigger.personaName === ravn.personaName),
    [triggers, ravn.personaName],
  );
  const ravnSessions = useMemo(
    () =>
      (sessions ?? []).filter(
        (session) =>
          session.ravnId === ravn.id &&
          (!ravn.instanceId || session.instanceId === ravn.instanceId),
      ),
    [sessions, ravn.id, ravn.instanceId],
  );

  const openSession = ravnSessions.find((session) => session.status === 'running');

  const tabs: Array<{ id: TabId; label: string; count?: number }> = [
    { id: 'overview', label: 'Overview' },
    { id: 'triggers', label: 'Triggers', count: ravnTriggers.length },
    { id: 'activity', label: 'Activity', count: activityMessages.length },
    { id: 'sessions', label: 'Sessions', count: ravnSessions.length },
    { id: 'connectivity', label: 'Connectivity' },
  ];

  // A stored tab can point at a section this ravn doesn't have (e.g. the
  // removed "chat" tab persisted before it was consolidated into Sessions).
  const resolvedTab: TabId = tabs.some((tab) => tab.id === activeTab) ? activeTab : 'overview';

  const subtitle = detailSubtitle(ravn);
  const canRestart = Boolean(
    ravn.managed &&
    ravn.capabilities?.includes('runtime.restart') &&
    ravn.desiredState === 'running' &&
    ['active', 'failed'].includes(ravn.observedState ?? ''),
  );
  const hasSuspend = Boolean(ravn.managed && ravn.capabilities?.includes('runtime.suspend'));
  const isSuspended = ravn.observedState === 'suspended' || ravn.desiredState === 'suspended';
  const canSuspend = hasSuspend && ravn.observedState === 'active' && !isSuspended;
  const canResume = hasSuspend && isSuspended;

  async function removeResident() {
    try {
      await deleteResident.mutateAsync(ravn);
    } catch {
      return;
    }
    setDeleteOpen(false);
    onDeleted?.();
  }

  function selectTab(tabId: TabId) {
    saveStorage(TAB_STORAGE_KEY, tabId);
    setActiveTab(tabId);
  }

  return (
    <div className="rv-detail" data-testid="ravn-detail">
      <header className="rv-detail__hero">
        <div className="rv-detail__hero-left">
          <PersonaAvatar
            role={ravn.role ?? 'build'}
            letter={ravn.letter ?? nameForRavn(ravn).charAt(0).toUpperCase()}
            size={46}
          />
          <div>
            <div className="rv-detail__title-wrap">
              <h1 className="rv-detail__title">{nameForRavn(ravn)}</h1>
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

          {canRestart && (
            <button
              type="button"
              className="rv-icon-btn"
              onClick={() => lifecycle.mutate({ ravn, action: 'restart' })}
              disabled={lifecycle.isPending}
              title="Restart resident"
              aria-label="Restart resident"
              data-testid="resident-restart"
            >
              <RotateCw size={15} aria-hidden="true" />
            </button>
          )}

          {canSuspend && (
            <button
              type="button"
              className="rv-icon-btn"
              onClick={() => lifecycle.mutate({ ravn, action: 'suspend' })}
              disabled={lifecycle.isPending}
              title="Suspend resident"
              aria-label="Suspend resident"
              data-testid="resident-suspend"
            >
              <Pause size={15} aria-hidden="true" />
            </button>
          )}

          {canResume && (
            <button
              type="button"
              className="rv-icon-btn"
              onClick={() => lifecycle.mutate({ ravn, action: 'resume' })}
              disabled={lifecycle.isPending}
              title="Resume resident"
              aria-label="Resume resident"
              data-testid="resident-resume"
            >
              <Play size={15} aria-hidden="true" />
            </button>
          )}

          {openSession && (
            <button
              type="button"
              className="rv-action-btn rv-action-btn--primary"
              onClick={() => dispatchSessionSelection(openSession)}
            >
              <MessageSquare size={14} aria-hidden="true" />
              Open session
            </button>
          )}

          {ravn.managed && ravn.observedState !== 'deleting' && (
            <button
              type="button"
              className="rv-icon-btn rv-icon-btn--danger"
              onClick={() => {
                deleteResident.reset();
                setDeleteOpen(true);
              }}
              title="Delete resident"
              aria-label="Delete resident"
              data-testid="resident-delete-open"
            >
              <Trash2 size={15} aria-hidden="true" />
            </button>
          )}

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

      <nav
        className="rv-sectabs"
        aria-label="Ravn detail sections"
        role="tablist"
        data-testid="ravn-sectabs"
      >
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            id={`ravn-tab-${tab.id}`}
            aria-controls={`ravn-panel-${tab.id}`}
            aria-selected={resolvedTab === tab.id}
            onClick={() => selectTab(tab.id)}
            onKeyDown={(event) => {
              if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
              event.preventDefault();
              const currentIndex = tabs.findIndex((candidate) => candidate.id === tab.id);
              const offset = event.key === 'ArrowRight' ? 1 : -1;
              const next = tabs[(currentIndex + offset + tabs.length) % tabs.length]!;
              selectTab(next.id);
              document.getElementById(`ravn-tab-${next.id}`)?.focus();
            }}
            className={`rv-sectab${resolvedTab === tab.id ? ' rv-sectab--active' : ''}`}
            data-testid={`sectab-${tab.id}`}
          >
            {tab.label}
            {tab.count != null && tab.count > 0 && (
              <span className="rv-sectabs-n">{tab.count}</span>
            )}
          </button>
        ))}
      </nav>

      <div
        className="rv-detail__content"
        id={`ravn-panel-${resolvedTab}`}
        role="tabpanel"
        aria-labelledby={`ravn-tab-${resolvedTab}`}
      >
        {lifecycle.isError && (
          <div className="rv-inline-error" role="alert">
            {lifecycle.error instanceof Error ? lifecycle.error.message : 'Resident command failed'}
          </div>
        )}
        {resolvedTab === 'overview' && (
          <OverviewSection ravn={ravn} budget={budget} sessions={ravnSessions} />
        )}
        {resolvedTab === 'triggers' && <TriggersSection triggers={ravnTriggers} />}
        {resolvedTab === 'activity' && (
          <ActivitySection
            messages={activityMessages}
            isActive={ravn.status === 'active'}
            isLoading={activityLoading}
          />
        )}
        {resolvedTab === 'sessions' && <SessionsSection ravn={ravn} sessions={ravnSessions} />}
        {resolvedTab === 'connectivity' && <ConnectivitySection ravn={ravn} />}
      </div>

      <Dialog
        open={deleteOpen}
        onOpenChange={(open) => {
          setDeleteOpen(open);
          if (!open) deleteResident.reset();
        }}
      >
        <DialogContent
          title="Delete resident"
          description="This removes the resident runtime and all resources owned by its deployment backend."
        >
          {deleteResident.isError && (
            <div className="rv-form-error" role="alert">
              {deleteResident.error instanceof Error
                ? deleteResident.error.message
                : 'Resident deletion failed'}
            </div>
          )}
          <div className="rv-form-actions">
            <button type="button" className="rv-action-btn" onClick={() => setDeleteOpen(false)}>
              Cancel
            </button>
            <button
              type="button"
              className="rv-action-btn rv-action-btn--danger"
              onClick={() => void removeResident()}
              disabled={deleteResident.isPending}
              data-testid="resident-delete-confirm"
            >
              {deleteResident.isPending ? 'Deleting…' : 'Delete'}
            </button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
