import { useEffect, useMemo } from 'react';
import { Sparkline, StateDot } from '@niuulabs/ui';
import type { DotState } from '@niuulabs/ui';
import type { TopologyNode, Topology, Registry, NodeActivity } from '../../domain';
import './EntityDrawer.css';

export interface EntityDrawerProps {
  /** The node to display in the drawer. Null means drawer is closed. */
  node: TopologyNode | null;
  topology: Topology | null;
  registry: Registry | null;
  onClose: () => void;
  onNodeSelect?: (node: TopologyNode) => void;
}

// ── Activity dot ──────────────────────────────────────────────────────────────

const ACTIVITY_COLOR: Record<NodeActivity, string> = {
  idle: 'var(--color-text-muted)',
  thinking: 'var(--brand-300, var(--color-brand))',
  tooling: 'var(--brand-400, var(--color-brand))',
  waiting: 'var(--color-text-muted)',
  delegating: 'var(--brand-200, var(--color-brand))',
  writing: 'var(--brand-200, var(--color-brand))',
  reading: 'var(--brand-300, var(--color-brand))',
};

function ActivityDot({ activity }: { activity: NodeActivity }) {
  const color = ACTIVITY_COLOR[activity] ?? 'var(--color-text-muted)';
  const pulsing = activity !== 'idle';
  return (
    <span
      className={`obs-entity-drawer__activity-dot${pulsing ? ' obs-entity-drawer__activity-dot--pulse' : ''}`}
      style={{ background: color, boxShadow: pulsing ? `0 0 8px ${color}` : 'none' }}
      aria-label={activity}
    />
  );
}

// ── Kind-specific properties ──────────────────────────────────────────────────

function KindProperties({ node }: { node: TopologyNode }) {
  const kind = node.typeId;

  if (
    ![
      'tyr',
      'bifrost',
      'volundr',
      'ravn_long',
      'valkyrie',
      'host',
      'printer',
      'vaettir',
      'service',
      'model',
    ].includes(kind)
  ) {
    return null;
  }

  return (
    <section className="obs-entity-drawer__section">
      <h4 className="obs-entity-drawer__section-title">Properties</h4>
      <dl className="obs-entity-drawer__prop-grid">
        {kind === 'tyr' && (
          <>
            <dt>mode</dt>
            <dd>
              <span className="obs-entity-drawer__badge" data-mode={node.mode}>
                {node.mode ?? '—'}
              </span>
            </dd>
            <dt>active sagas</dt>
            <dd>{node.activeSagas ?? 0}</dd>
            <dt>pending raids</dt>
            <dd>{node.pendingRaids ?? 0}</dd>
          </>
        )}
        {kind === 'bifrost' && (
          <>
            <dt>providers</dt>
            <dd>{node.providers?.join(', ') ?? '—'}</dd>
            <dt>req/min</dt>
            <dd>{node.reqPerMin ?? 0}</dd>
            <dt>cache hit</dt>
            <dd>
              {node.cacheHitRate !== undefined ? `${Math.round(node.cacheHitRate * 100)}%` : '—'}
            </dd>
          </>
        )}
        {kind === 'volundr' && (
          <>
            <dt>sessions</dt>
            <dd>
              {node.activeSessions ?? 0} / {node.maxSessions ?? 0}
            </dd>
          </>
        )}
        {kind === 'ravn_long' && (
          <>
            {node.persona && (
              <>
                <dt>persona</dt>
                <dd>{node.persona}</dd>
              </>
            )}
            {node.specialty && (
              <>
                <dt>specialty</dt>
                <dd className="obs-entity-drawer__plain">{node.specialty}</dd>
              </>
            )}
            <dt>tokens</dt>
            <dd>{node.tokens !== undefined ? node.tokens.toLocaleString() : '—'}</dd>
          </>
        )}
        {kind === 'valkyrie' && (
          <>
            <dt>specialty</dt>
            <dd className="obs-entity-drawer__plain">{node.specialty ?? '—'}</dd>
            <dt>autonomy</dt>
            <dd>
              <span className="obs-entity-drawer__badge" data-autonomy={node.autonomy}>
                {node.autonomy ?? '—'}
              </span>
            </dd>
          </>
        )}
        {kind === 'host' && (
          <>
            <dt>hardware</dt>
            <dd className="obs-entity-drawer__plain">{node.hw ?? '—'}</dd>
            <dt>os</dt>
            <dd>{node.os ?? '—'}</dd>
            {node.cores !== undefined && (
              <>
                <dt>cores</dt>
                <dd>{node.cores}</dd>
              </>
            )}
            {node.ram && (
              <>
                <dt>ram</dt>
                <dd>{node.ram}</dd>
              </>
            )}
            {node.gpu && (
              <>
                <dt>gpu</dt>
                <dd>{node.gpu}</dd>
              </>
            )}
          </>
        )}
        {kind === 'printer' && (
          <>
            <dt>model</dt>
            <dd>{node.model ?? '—'}</dd>
          </>
        )}
        {kind === 'vaettir' && (
          <>
            <dt>sensors</dt>
            <dd>{node.sensors ?? '—'}</dd>
          </>
        )}
        {kind === 'service' && (
          <>
            <dt>type</dt>
            <dd>{node.svcType ?? '—'}</dd>
          </>
        )}
        {kind === 'model' && (
          <>
            <dt>provider</dt>
            <dd>{node.provider ?? '—'}</dd>
            <dt>location</dt>
            <dd>{node.location ?? '—'}</dd>
          </>
        )}
      </dl>
    </section>
  );
}

// ── Realm drawer body ─────────────────────────────────────────────────────────

interface RealmDrawerProps {
  node: TopologyNode;
  topology: Topology | null;
  onNodeSelect?: (node: TopologyNode) => void;
}

function RealmDrawer({ node, topology, onNodeSelect }: RealmDrawerProps) {
  const residents = topology ? topology.nodes.filter((n) => n.parentId === node.id) : [];

  return (
    <>
      <div className="obs-entity-drawer__head">
        <div className="obs-entity-drawer__identity">
          <span className="obs-entity-drawer__rune" aria-hidden="true">
            ᛞ
          </span>
          <div className="obs-entity-drawer__meta">
            <span className="obs-entity-drawer__type-label">Realm · VLAN zone</span>
            {node.vlan !== undefined && (
              <span className="obs-entity-drawer__id-chip">vlan {node.vlan}</span>
            )}
          </div>
        </div>
      </div>
      <div className="obs-entity-drawer__body">
        {node.purpose && <p className="obs-entity-drawer__description">{node.purpose}</p>}
        <section className="obs-entity-drawer__section">
          <h4 className="obs-entity-drawer__section-title">About</h4>
          <dl className="obs-entity-drawer__prop-grid">
            {node.vlan !== undefined && (
              <>
                <dt>vlan</dt>
                <dd>{node.vlan}</dd>
              </>
            )}
            {node.dns && (
              <>
                <dt>dns</dt>
                <dd>{node.dns}</dd>
              </>
            )}
            <dt>residents</dt>
            <dd>{residents.length}</dd>
          </dl>
        </section>
        {residents.length > 0 && (
          <section className="obs-entity-drawer__section">
            <h4 className="obs-entity-drawer__section-title">Residents</h4>
            <ul className="obs-entity-drawer__resident-list">
              {residents.slice(0, 20).map((resident) => (
                <li key={resident.id}>
                  <button
                    className="obs-entity-drawer__resident-btn"
                    onClick={() => onNodeSelect?.(resident)}
                    data-testid={`resident-${resident.id}`}
                  >
                    {resident.activity && <ActivityDot activity={resident.activity} />}
                    <span className="obs-entity-drawer__resident-label">{resident.label}</span>
                    <span className="obs-entity-drawer__resident-kind">{resident.typeId}</span>
                  </button>
                </li>
              ))}
              {residents.length > 20 && (
                <li className="obs-entity-drawer__resident-overflow">
                  +{residents.length - 20} more
                </li>
              )}
            </ul>
          </section>
        )}
      </div>
    </>
  );
}

// ── Cluster drawer body ───────────────────────────────────────────────────────

interface ClusterDrawerProps {
  node: TopologyNode;
  topology: Topology | null;
  onNodeSelect?: (node: TopologyNode) => void;
}

function ClusterDrawer({ node, topology, onNodeSelect }: ClusterDrawerProps) {
  const members = topology ? topology.nodes.filter((n) => n.parentId === node.id) : [];

  return (
    <>
      <div className="obs-entity-drawer__head">
        <div className="obs-entity-drawer__identity">
          <span className="obs-entity-drawer__rune" aria-hidden="true">
            ᚲ
          </span>
          <div className="obs-entity-drawer__meta">
            <span className="obs-entity-drawer__type-label">Cluster · k8s</span>
            {node.parentId && (
              <span className="obs-entity-drawer__id-chip">
                realm · {node.zone ?? node.parentId}
              </span>
            )}
          </div>
        </div>
      </div>
      <div className="obs-entity-drawer__body">
        <section className="obs-entity-drawer__section">
          <h4 className="obs-entity-drawer__section-title">About</h4>
          {node.purpose && <p className="obs-entity-drawer__description">{node.purpose}</p>}
          <dl className="obs-entity-drawer__prop-grid">
            {node.zone && (
              <>
                <dt>realm</dt>
                <dd>{node.zone}</dd>
              </>
            )}
            <dt>members</dt>
            <dd>{members.length}</dd>
          </dl>
        </section>
        {members.length > 0 && (
          <section className="obs-entity-drawer__section">
            <h4 className="obs-entity-drawer__section-title">Members</h4>
            <ul className="obs-entity-drawer__resident-list">
              {members.map((member) => (
                <li key={member.id}>
                  <button
                    className="obs-entity-drawer__resident-btn"
                    onClick={() => onNodeSelect?.(member)}
                    data-testid={`resident-${member.id}`}
                  >
                    {member.activity && <ActivityDot activity={member.activity} />}
                    <span className="obs-entity-drawer__resident-label">{member.label}</span>
                    <span className="obs-entity-drawer__resident-kind">{member.typeId}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </>
  );
}

// ── Main entity drawer ────────────────────────────────────────────────────────

export function EntityDrawer({
  node,
  topology,
  registry,
  onClose,
  onNodeSelect,
}: EntityDrawerProps) {
  const entityType = node ? registry?.types.find((t) => t.id === node.typeId) : undefined;
  const residents = node && topology ? topology.nodes.filter((n) => n.parentId === node.id) : [];
  const isRealm = node?.typeId === 'realm';
  const isCluster = node?.typeId === 'cluster';
  const isContainer = ['realm', 'cluster', 'host'].includes(node?.typeId ?? '');

  // Sparkline seed: deterministic per-entity pseudo-random values.
  const sparkValues = useMemo(() => {
    if (!node) return [];
    const seed = node.id.charCodeAt(0) + node.id.charCodeAt(node.id.length - 1);
    return Array.from(
      { length: 24 },
      (_, i) => 30 + Math.sin(i * 0.7 + seed) * 15 + (Math.sin(i * 1.3 + seed * 3) * 10 + 10),
    );
  }, [node]);

  const showSparkline = ['ravn_long', 'bifrost'].includes(node?.typeId ?? '');

  useEffect(() => {
    if (!node) return;
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [node, onClose]);

  if (!node) return null;

  return (
    <aside role="dialog" aria-label={node.label} className="obs-entity-drawer__panel">
      <button className="obs-entity-drawer__close-btn" aria-label="Close" onClick={onClose}>
        <span aria-hidden="true">✕</span>
      </button>

      {isRealm && <RealmDrawer node={node} topology={topology} onNodeSelect={onNodeSelect} />}
      {isCluster && <ClusterDrawer node={node} topology={topology} onNodeSelect={onNodeSelect} />}
      {!isRealm && !isCluster && (
        <>
          {/* HEAD — rune · label · activity · status · timestamp */}
          <div className="obs-entity-drawer__head">
            <div className="obs-entity-drawer__identity">
              {entityType && (
                <span className="obs-entity-drawer__rune" aria-hidden="true">
                  {entityType.rune}
                </span>
              )}
              <div className="obs-entity-drawer__meta">
                <span className="obs-entity-drawer__type-label">
                  {entityType?.label ?? node.typeId} · entity
                </span>
                <div className="obs-entity-drawer__status">
                  <StateDot state={node.status as DotState} />
                  <span className="obs-entity-drawer__status-text">{node.status}</span>
                </div>
              </div>
            </div>
            {showSparkline && <Sparkline id={node.id} width={120} height={28} />}
          </div>

          {/* BODY */}
          <div className="obs-entity-drawer__body">
            {/* Activity row */}
            {node.activity && (
              <div className="obs-entity-drawer__activity-row">
                <ActivityDot activity={node.activity} />
                <span className="obs-entity-drawer__activity-label">
                  {node.activity.toUpperCase()}
                </span>
                <span className="obs-entity-drawer__activity-ts">
                  last tick · {topology?.timestamp.slice(11, 19) ?? '--:--'}
                </span>
              </div>
            )}

            {entityType?.description && (
              <p className="obs-entity-drawer__description">
                {entityType.description.split('.')[0]}.
              </p>
            )}

            {/* Identity section */}
            <section className="obs-entity-drawer__section">
              <h4 className="obs-entity-drawer__section-title">Identity</h4>
              <dl className="obs-entity-drawer__prop-grid">
                <dt>id</dt>
                <dd>{node.id}</dd>
                <dt>kind</dt>
                <dd>{node.typeId}</dd>
                {node.zone && (
                  <>
                    <dt>realm</dt>
                    <dd>{node.zone}</dd>
                  </>
                )}
                {node.cluster && (
                  <>
                    <dt>cluster</dt>
                    <dd>{node.cluster}</dd>
                  </>
                )}
                {node.hostId && (
                  <>
                    <dt>host</dt>
                    <dd>
                      <button
                        className="obs-entity-drawer__link"
                        onClick={() => {
                          const hostNode = topology?.nodes.find((n) => n.id === node.hostId);
                          if (hostNode) onNodeSelect?.(hostNode);
                        }}
                      >
                        {node.hostId}
                      </button>
                    </dd>
                  </>
                )}
                {node.flockId && node.flockId !== 'long' && (
                  <>
                    <dt>flock</dt>
                    <dd>{node.flockId}</dd>
                  </>
                )}
              </dl>
            </section>

            {/* Kind-specific properties */}
            <KindProperties node={node} />

            {/* Residents for host containers */}
            {isContainer && residents.length > 0 && (
              <section className="obs-entity-drawer__section">
                <h4 className="obs-entity-drawer__section-title">Residents</h4>
                <ul className="obs-entity-drawer__resident-list">
                  {residents.map((resident) => {
                    const residentType = registry?.types.find((t) => t.id === resident.typeId);
                    return (
                      <li key={resident.id}>
                        <button
                          className="obs-entity-drawer__resident-btn"
                          onClick={() => onNodeSelect?.(resident)}
                          data-testid={`resident-${resident.id}`}
                        >
                          {residentType && (
                            <span className="obs-entity-drawer__resident-rune" aria-hidden="true">
                              {residentType.rune}
                            </span>
                          )}
                          <span className="obs-entity-drawer__resident-label">
                            {resident.label}
                          </span>
                          <StateDot state={resident.status as DotState} />
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </section>
            )}

            {/* Coordinator confidence section */}
            {node.role === 'coord' && node.confidence !== undefined && (
              <section className="obs-entity-drawer__section">
                <h4 className="obs-entity-drawer__section-title">Coordinator</h4>
                <dl className="obs-entity-drawer__prop-grid">
                  <dt>confidence</dt>
                  <dd className="obs-entity-drawer__conf-bar">
                    <span className="obs-entity-drawer__conf-track">
                      <span
                        className="obs-entity-drawer__conf-fill"
                        style={{ width: `${node.confidence * 100}%` }}
                      />
                    </span>
                    <span>{Math.round(node.confidence * 100)}%</span>
                  </dd>
                </dl>
              </section>
            )}

            {/* Token throughput sparkline */}
            {showSparkline && sparkValues.length > 0 && (
              <section className="obs-entity-drawer__section">
                <h4 className="obs-entity-drawer__section-title">Token throughput · 24 ticks</h4>
                <svg
                  className="obs-entity-drawer__sparkline"
                  viewBox="0 0 240 32"
                  preserveAspectRatio="none"
                  aria-hidden="true"
                >
                  <polyline
                    points={sparkValues.map((v, i) => `${i * 10},${32 - v * 0.5}`).join(' ')}
                    fill="none"
                    stroke="var(--brand-300, var(--color-brand))"
                    strokeWidth="1.2"
                  />
                  <polyline
                    points={`0,32 ${sparkValues.map((v, i) => `${i * 10},${32 - v * 0.5}`).join(' ')} 240,32`}
                    fill="color-mix(in srgb, var(--brand-300, var(--color-brand)) 15%, transparent)"
                    stroke="none"
                  />
                </svg>
              </section>
            )}

            {/* Actions */}
            <section className="obs-entity-drawer__section">
              <h4 className="obs-entity-drawer__section-title">Actions</h4>
              <div className="obs-entity-drawer__actions">
                <button className="obs-entity-drawer__btn obs-entity-drawer__btn--primary">
                  Open chat
                </button>
                <button className="obs-entity-drawer__btn">Inspect in registry</button>
                <button className="obs-entity-drawer__btn obs-entity-drawer__btn--ghost">
                  Quarantine
                </button>
              </div>
            </section>
          </div>
        </>
      )}
    </aside>
  );
}
