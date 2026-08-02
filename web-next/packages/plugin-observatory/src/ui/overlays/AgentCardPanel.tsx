import { useMemo } from 'react';
import type { AgentDirectoryEntry, TopologyNode } from '../../domain';
import { useAgents } from '../../application/useAgents';
import './AgentCardPanel.css';

export interface AgentCardPanelProps {
  /** The node the drawer is showing. Null closes the panel. */
  node: TopologyNode | null;
  /** `json` shows the card exactly as served, for copying into a request. */
  mode?: 'card' | 'json';
}

/** Capability flags every A2A card is expected to declare. */
const CAPABILITY_LABELS: ReadonlyArray<[key: string, label: string]> = [
  ['streaming', 'streaming'],
  ['pushNotifications', 'push'],
  ['stateTransitionHistory', 'history'],
];

function capabilityEnabled(capabilities: Record<string, unknown>, key: string): boolean {
  return capabilities[key] === true;
}

/** A definition row. An absent value is a dash, never a blank. */
function Row({
  label,
  value,
  testId,
  mono,
}: {
  label: string;
  value?: string;
  testId?: string;
  mono?: boolean;
}) {
  return (
    <>
      <dt>{label}</dt>
      <dd data-testid={testId} className={mono ? 'obs-agent-card__mono' : undefined}>
        {value || '—'}
      </dd>
    </>
  );
}

/**
 * How a caller authenticates. Read off the declared security schemes rather
 * than assumed: an agent that advertises none is a different claim from one
 * whose scheme we failed to read.
 */
function authOf(entry: AgentDirectoryEntry): string {
  const names = Object.keys(entry.securitySchemes ?? {});
  if (names.length === 0) return '';
  const flows = entry.securityRequirements
    .flatMap((requirement) => Object.values(requirement))
    .flat()
    .filter((scope): scope is string => typeof scope === 'string');
  return [names.join(' · '), ...new Set(flows)].join(' · ');
}

/** Skill ids are snake_case identifiers; the reader gets both forms. */
function skillName(id: string): string {
  const words = id.replace(/[_-]+/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function signatureLabel(entry: AgentDirectoryEntry): { text: string; state: string } {
  if (entry.signatureVerified === true) return { text: 'signature verified', state: 'ok' };
  if (entry.signatureVerified === false) return { text: 'signature invalid', state: 'bad' };
  return { text: 'unsigned', state: 'unknown' };
}

/**
 * The A2A card a topology node publishes.
 *
 * Not every node is an agent, so the panel renders nothing rather than an empty
 * shell when the directory has no entry projecting this node.
 */
export function AgentCardPanel({ node, mode = 'card' }: AgentCardPanelProps) {
  const { data, isLoading, isError, error } = useAgents();

  const entry = useMemo<AgentDirectoryEntry | null>(() => {
    if (!node || !data) return null;
    return data.items.find((item) => item.topologyNodeId === node.id) ?? null;
  }, [data, node]);

  if (!node) return null;

  if (isLoading) {
    return (
      <section className="obs-agent-card" data-testid="agent-card-loading">
        <h3 className="obs-agent-card__title">A2A card</h3>
        <p className="obs-agent-card__muted">Resolving agent directory…</p>
      </section>
    );
  }

  if (isError) {
    return (
      <section className="obs-agent-card" data-testid="agent-card-error">
        <h3 className="obs-agent-card__title">A2A card</h3>
        <p className="obs-agent-card__error">
          Agent directory unavailable{error instanceof Error ? ` — ${error.message}` : ''}
        </p>
      </section>
    );
  }

  // A host, a service or a realm simply has no card. Say nothing.
  if (!entry) return null;

  const iface = entry.supportedInterfaces[0];
  const signature = signatureLabel(entry);

  if (mode === 'json') {
    // The card exactly as served, so it can be copied into a request.
    return (
      <pre className="obs-agent-card__json" data-testid="agent-card-json">
        {JSON.stringify(entry, null, 2)}
      </pre>
    );
  }

  return (
    <div className="obs-agent-card" data-testid="agent-card">
      <section className="obs-agent-card__block">
        <h3 className="obs-agent-card__title">Agent card</h3>
        {entry.description ? (
          <p className="obs-agent-card__description">{entry.description}</p>
        ) : null}
        <dl className="obs-agent-card__facts">
          <Row label="card" value={entry.cardUrl} testId="agent-card-url" mono />
          <Row label="transport" value={iface?.protocolBinding} />
          <Row
            label="protocol"
            value={iface?.protocolVersion ? `A2A ${iface.protocolVersion}` : ''}
          />
          <Row label="visibility" value={entry.visibility} />
          <Row label="auth" value={authOf(entry)} />
        </dl>
      </section>

      <section className="obs-agent-card__block">
        <h3 className="obs-agent-card__title">Capabilities</h3>
        <div className="obs-agent-card__caps" data-testid="agent-card-capabilities">
          {CAPABILITY_LABELS.map(([key, label]) => {
            const on = capabilityEnabled(entry.capabilities, key);
            return (
              <span
                key={key}
                className={`obs-agent-card__cap${on ? ' obs-agent-card__cap--on' : ''}`}
                data-testid={`agent-cap-${key}`}
                data-enabled={on}
              >
                {on ? '✓ ' : '· '}
                {label}
              </span>
            );
          })}
          <span
            className={`obs-agent-card__cap obs-agent-card__cap--sig-${signature.state}`}
            data-testid="agent-card-signature"
          >
            {signature.text}
          </span>
        </div>
      </section>

      <section className="obs-agent-card__block">
        <h3 className="obs-agent-card__title">
          Skills it advertises
          <span className="obs-agent-card__count">{entry.skillIds.length}</span>
        </h3>
        {entry.skillIds.length === 0 ? (
          <p className="obs-agent-card__muted">None advertised.</p>
        ) : (
          <ul className="obs-agent-card__skills" data-testid="agent-card-skills">
            {entry.skillIds.map((skill) => (
              <li key={skill}>
                <span className="obs-agent-card__skill-id">{skill}</span>
                <span className="obs-agent-card__skill-name">{skillName(skill)}</span>
                {entry.tags.length > 0 ? (
                  <em className="obs-agent-card__skill-tag">{entry.tags[0]}</em>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="obs-agent-card__block">
        <h3 className="obs-agent-card__title">Reachable at</h3>
        <dl className="obs-agent-card__facts">
          <Row label="chat" value={iface?.url} testId="agent-card-endpoint" mono />
          <Row label="tenant" value={iface?.tenant} />
          <Row label="session" value={entry.sourceInstanceId} testId="agent-card-session" />
        </dl>
      </section>
    </div>
  );
}
