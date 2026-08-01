import { useMemo } from 'react';
import type { AgentDirectoryEntry, TopologyNode } from '../../domain';
import { useAgents } from '../../application/useAgents';
import './AgentCardPanel.css';

export interface AgentCardPanelProps {
  /** The node the drawer is showing. Null closes the panel. */
  node: TopologyNode | null;
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
export function AgentCardPanel({ node }: AgentCardPanelProps) {
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

  return (
    <section className="obs-agent-card" data-testid="agent-card">
      <h3 className="obs-agent-card__title">
        A2A card
        <span className="obs-agent-card__kind" data-testid="agent-card-kind">
          {entry.kind}
        </span>
      </h3>

      {entry.description ? (
        <p className="obs-agent-card__description">{entry.description}</p>
      ) : null}

      <dl className="obs-agent-card__facts">
        <dt>card</dt>
        <dd data-testid="agent-card-url">{entry.cardUrl}</dd>
        {iface ? (
          <>
            <dt>transport</dt>
            <dd>{iface.protocolBinding}</dd>
            <dt>protocol</dt>
            <dd>A2A {iface.protocolVersion}</dd>
          </>
        ) : null}
        <dt>visibility</dt>
        <dd>{entry.visibility}</dd>
        <dt>version</dt>
        <dd>{entry.cardVersion}</dd>
      </dl>

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

      {entry.skillIds.length > 0 ? (
        <>
          <h4 className="obs-agent-card__subtitle">Skills · {entry.skillIds.length}</h4>
          <ul className="obs-agent-card__skills" data-testid="agent-card-skills">
            {entry.skillIds.map((skill) => (
              <li key={skill}>{skill}</li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}
