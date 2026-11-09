import { useEffect, useState, type CSSProperties } from 'react';
import { Check, Circle, CircleDot, File as FileIcon } from 'lucide-react';
import {
  useConversationResources,
  verdictLabel,
  verdictTone,
  type OutcomeTone,
  type PresentedFileRef,
  type SessionAgent,
  type Worker,
} from '@niuulabs/ui';
import type { Session } from '../../domain/session';
import type { ChecklistItem } from '../../domain/sessionAttention';
import { SESSION_STATE_LABELS } from '../sessions/presentation';
import { PanelSection } from './PanelSection';
import { agentDuration, stateTone, type SessionInfoRow } from './sessionHeaderModel';

const CHECKLIST_ICON = {
  completed: Check,
  in_progress: CircleDot,
  pending: Circle,
} as const;

/** The session's plan, or the agent's own task list on runtimes that send no plan. */
export function PlanSection({ checklist }: { checklist: readonly ChecklistItem[] }) {
  if (checklist.length === 0) return null;
  const done = checklist.filter((item) => item.status === 'completed').length;
  return (
    <PanelSection title="Plan" count={`${done}/${checklist.length}`}>
      <ul className="niuu-session-panel__checklist">
        {checklist.map((item, index) => {
          const Icon = CHECKLIST_ICON[item.status];
          return (
            <li key={`${index}:${item.label}`} data-status={item.status}>
              <Icon aria-hidden="true" />
              <span>{item.label}</span>
            </li>
          );
        })}
      </ul>
    </PanelSection>
  );
}

/** How often a running agent's elapsed time is redrawn. */
const AGENT_CLOCK_INTERVAL_MS = 1000;

/** The current time, advancing only while something on screen is still running. */
function useRunningClock(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), AGENT_CLOCK_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}

export function AgentsSection({ agents }: { agents: readonly SessionAgent[] }) {
  const now = useRunningClock(agents.some((agent) => !agent.endedAt));
  if (agents.length === 0) return null;
  return (
    <PanelSection title="Agents" count={agents.length}>
      <ul className="niuu-session-panel__agents">
        {agents.map((agent) => {
          const duration = agentDuration(agent, now);
          return (
            <li key={agent.id} title={agent.description}>
              <strong>{agent.name}</strong>
              <small data-status={agent.status}>
                {agent.status}
                {duration ? ` · ${duration}` : ''}
              </small>
            </li>
          );
        })}
      </ul>
    </PanelSection>
  );
}

export function ChangedFilesSection({
  count,
  stats,
  onOpenDiffs,
}: {
  count?: number;
  stats?: { added: number; modified: number; deleted: number } | null;
  onOpenDiffs: () => void;
}) {
  if (!count) return null;
  const parts = stats
    ? [
        stats.added ? `${stats.added} added` : null,
        stats.modified ? `${stats.modified} modified` : null,
        stats.deleted ? `${stats.deleted} deleted` : null,
      ].filter((part): part is string => part !== null)
    : [];
  return (
    <PanelSection title="Changed files" count={count}>
      <button type="button" className="niuu-session-panel__link" onClick={onOpenDiffs}>
        {count} file{count === 1 ? '' : 's'} changed
      </button>
      {parts.length > 0 ? <p className="niuu-session-panel__quiet">{parts.join(' · ')}</p> : null}
    </PanelSection>
  );
}

/** Files the session presented, opened in the same preview the chat links use. */
export function DeliverablesSection({ files }: { files: readonly PresentedFileRef[] }) {
  const resources = useConversationResources();
  if (files.length === 0) return null;
  if (!resources) {
    throw new Error('DeliverablesSection must render inside SessionResources, which opens files.');
  }
  // The same file can be presented more than once; the list shows it once.
  const unique = [...new Map(files.map((file) => [file.fileId, file])).values()];
  return (
    <PanelSection title="Deliverables" count={unique.length}>
      <ul className="niuu-session-panel__files">
        {unique.map((file) => (
          <li key={file.fileId}>
            <button
              type="button"
              title={file.path ?? file.name}
              onClick={() =>
                resources.open({
                  kind: 'presented',
                  path: file.fileId,
                  name: file.name,
                  ...(file.mime ? { mime: file.mime } : {}),
                  ...(file.sessionId ? { sessionId: file.sessionId } : {}),
                })
              }
            >
              <FileIcon aria-hidden="true" />
              <span>{file.name}</span>
            </button>
          </li>
        ))}
      </ul>
    </PanelSection>
  );
}

export function SessionInfoSection({ rows }: { rows: readonly SessionInfoRow[] }) {
  if (rows.length === 0) return null;
  return (
    <PanelSection title="Session">
      <dl className="niuu-session-panel__kv">
        {rows.map((row) => (
          <div key={row.id}>
            <dt>{row.label}</dt>
            <dd className={row.mono ? 'niuu-session-panel__mono' : undefined} title={row.value}>
              <span data-harness={row.harness}>{row.value}</span>
              {row.detail ? ` · ${row.detail}` : ''}
            </dd>
          </div>
        ))}
      </dl>
    </PanelSection>
  );
}

/** A Ravn worker with no outcome yet is known by whether it is working. */
function ravnWorkerPill(worker: Worker): { label: string; tone: OutcomeTone } | null {
  if (worker.latest) {
    return { label: verdictLabel(worker.latest.verdict), tone: verdictTone(worker.latest.verdict) };
  }
  if (worker.status === 'working') return { label: 'Working', tone: 'info' };
  if (worker.status === 'error') return { label: 'Error', tone: 'blocked' };
  if (worker.status === 'left') return { label: 'Left', tone: 'info' };
  return null;
}

/**
 * The workers a coordinator leads: the sessions it started (each opens its session) and
 * the Ravn workers in its room (each with the last outcome it reported).
 */
export function WorkersSection({
  sessions,
  ravnWorkers,
  onOpenSession,
}: {
  sessions: readonly Session[];
  ravnWorkers: readonly Worker[];
  onOpenSession: (id: string) => void;
}) {
  const count = sessions.length + ravnWorkers.length;
  if (count === 0) return null;
  return (
    <PanelSection title="Workers" count={count}>
      <ul className="niuu-session-panel__workers">
        {sessions.map((worker) => (
          <li key={`session:${worker.id}`}>
            <button
              type="button"
              className="niuu-session-panel__worker"
              data-testid={`panel-worker-${worker.id}`}
              onClick={() => onOpenSession(worker.id)}
            >
              <strong>{worker.name || worker.personaName || worker.id}</strong>
              <span className="niuu-session-panel__pill" data-tone={stateTone(worker.state)}>
                {SESSION_STATE_LABELS[worker.state]}
              </span>
              {worker.coordination?.objective ? (
                <small>{worker.coordination.objective}</small>
              ) : null}
            </button>
          </li>
        ))}
        {ravnWorkers.map((worker) => {
          const pill = ravnWorkerPill(worker);
          return (
            <li key={`ravn:${worker.peerId}`}>
              <div
                className="niuu-session-panel__worker"
                data-testid={`panel-ravn-${worker.peerId}`}
              >
                <strong
                  className="niuu-session-panel__worker-name"
                  style={{ '--worker-color': worker.color } as CSSProperties}
                >
                  {worker.name}
                </strong>
                {pill ? (
                  <span className="niuu-session-panel__pill" data-tone={pill.tone}>
                    {pill.label}
                  </span>
                ) : null}
                {worker.latest?.summary ? <small>{worker.latest.summary}</small> : null}
              </div>
            </li>
          );
        })}
      </ul>
    </PanelSection>
  );
}
