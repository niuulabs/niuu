import { useState } from 'react';
import { useService } from '@niuulabs/plugin-sdk';
import { modelLabel } from '@niuulabs/domain';
import { Dialog, DialogContent, EmptyState, ErrorState, LoadingState, cn } from '@niuulabs/ui';
import type { IVolundrService } from '../../ports/IVolundrService';
import type { Session, SessionState } from '../../domain/session';
import { sessionHarness } from '../../domain/session';
import { useSessionList } from '../hooks/useSessionStore';
import { SESSION_STATE_LABELS } from '../sessions/presentation';
import { errorText } from '../errorText';

/** States in which a session has a chat endpoint that accepts a message. */
const RECEIVING_STATES: ReadonlySet<SessionState> = new Set([
  'ready',
  'running',
  'idle',
  'awaiting_input',
]);

/** The text a target session receives: headed with where it came from, as Lexi does. */
export function forwardedText(fromSessionName: string, content: string): string {
  return `Forwarded from ${fromSessionName}:\n\n${content}`;
}

function sessionLabel(session: Session): string {
  return session.name || session.personaName || session.id;
}

function sessionModel(session: Session): string | null {
  if (session.model) return modelLabel(session.model);
  const harness = sessionHarness(session);
  if (harness === 'claude') return 'Claude';
  if (harness === 'codex') return 'Codex';
  return null;
}

/** Sessions that can receive a message, without the one it comes from, filtered by `query`. */
export function forwardTargets(
  sessions: readonly Session[],
  currentSessionId: string,
  query: string,
): Session[] {
  const needle = query.trim().toLowerCase();
  return sessions.filter((session) => {
    if (session.id === currentSessionId) return false;
    if (!RECEIVING_STATES.has(session.state)) return false;
    if (!needle) return true;
    return [
      sessionLabel(session),
      sessionModel(session),
      session.clusterName,
      session.clusterId,
      SESSION_STATE_LABELS[session.state],
    ].some((value) => value?.toLowerCase().includes(needle));
  });
}

/** What the page tells the person once the dialog has closed. */
export interface ForwardedNotice {
  targetName: string;
}

type Outcome =
  | { kind: 'idle' }
  | { kind: 'sending' }
  | { kind: 'pending'; targetName: string }
  | { kind: 'failed'; message: string };

export interface ForwardMessageDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The session the message comes from; it is not offered as a target. */
  currentSessionId: string;
  currentSessionName: string;
  /** The message text being forwarded. */
  content: string;
  /** The target confirmed delivery; the dialog has asked to close. */
  onDelivered: (notice: ForwardedNotice) => void;
}

function ForwardMessageForm({
  currentSessionId,
  currentSessionName,
  content,
  onDismiss,
  onDelivered,
}: Omit<ForwardMessageDialogProps, 'open' | 'onOpenChange'> & { onDismiss: () => void }) {
  const volundr = useService<IVolundrService>('volundr');
  const sessions = useSessionList();
  const [query, setQuery] = useState('');
  const [targetKey, setTargetKey] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Outcome>({ kind: 'idle' });

  const targets = forwardTargets(sessions.data ?? [], currentSessionId, query);
  const target = (sessions.data ?? []).find((session) => rowKey(session) === targetKey) ?? null;
  const sourceErrors = sessions.sources.filter((source) => source.error);
  const sending = outcome.kind === 'sending';
  const accepted = outcome.kind === 'pending';

  async function handleForward() {
    if (!target || sending) return;
    const targetName = sessionLabel(target);
    setOutcome({ kind: 'sending' });
    try {
      const delivery = await volundr.sendMessage(
        target.id,
        forwardedText(currentSessionName, content),
      );
      if (delivery.status === 'delivered') {
        onDelivered({ targetName });
        return;
      }
      setOutcome({ kind: 'pending', targetName });
    } catch (error) {
      setOutcome({ kind: 'failed', message: errorText(error, 'Forge refused the message') });
    }
  }

  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-3" data-testid="forward-message-dialog">
      <label className="niuu:flex niuu:flex-col niuu:gap-1.5 niuu:text-sm niuu:text-text-secondary">
        <span>Filter sessions</span>
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Name, model, host or state…"
          disabled={accepted}
          className="niuu:w-full niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:text-text-primary niuu:focus-visible:outline niuu:focus-visible:outline-2 niuu:focus-visible:outline-brand"
        />
      </label>

      {sessions.isLoading && <LoadingState label="Loading sessions…" />}
      {sessions.isError && (
        <ErrorState title="Could not list sessions" message={errorText(sessions.error)} />
      )}
      {sourceErrors.length > 0 && (
        <p role="alert" className="niuu:text-xs niuu:text-critical">
          {sourceErrors.map((source) => `${source.name}: ${source.error}`).join(' · ')}
        </p>
      )}
      {sessions.data && targets.length === 0 && (
        <EmptyState
          title={query.trim() ? 'No sessions match' : 'No other running sessions'}
          description={
            query.trim()
              ? 'Try a different name, model, host or state.'
              : 'Start or resume another session to forward this message to it.'
          }
        />
      )}
      {targets.length > 0 && (
        <ul
          className="niuu:max-h-72 niuu:overflow-y-auto niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-tertiary"
          data-testid="forward-target-list"
        >
          {targets.map((session) => {
            const model = sessionModel(session);
            const host = session.clusterName ?? session.clusterId;
            const key = rowKey(session);
            return (
              <li
                key={key}
                className="niuu:border-b niuu:border-border-subtle niuu:last:border-b-0"
              >
                <label
                  className={cn(
                    'niuu:flex niuu:cursor-pointer niuu:items-start niuu:gap-3 niuu:px-3 niuu:py-2.5',
                    'niuu:hover:bg-bg-secondary',
                    targetKey === key && 'niuu:bg-bg-secondary',
                  )}
                >
                  <input
                    type="radio"
                    name="forward-target"
                    value={key}
                    checked={targetKey === key}
                    onChange={() => setTargetKey(key)}
                    disabled={accepted}
                    className="niuu:mt-1"
                  />
                  <span className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col niuu:gap-0.5">
                    <span className="niuu:truncate niuu:text-sm niuu:text-text-primary">
                      {sessionLabel(session)}
                    </span>
                    <span className="niuu:flex niuu:flex-wrap niuu:gap-x-3 niuu:font-mono niuu:text-xs niuu:text-text-muted">
                      {model && <span>{model}</span>}
                      {host && <span>{host}</span>}
                      <span>{SESSION_STATE_LABELS[session.state]}</span>
                    </span>
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
      )}

      {outcome.kind === 'pending' && (
        <p
          role="status"
          className="niuu:text-sm niuu:text-text-secondary"
          data-testid="forward-pending"
        >
          Forge accepted the message for {outcome.targetName} and is still delivering it. It is not
          confirmed as sent yet.
        </p>
      )}
      {outcome.kind === 'failed' && (
        <p role="alert" className="niuu:text-sm niuu:text-critical" data-testid="forward-failed">
          {outcome.message}
        </p>
      )}

      <div className="niuu:flex niuu:justify-end niuu:gap-2">
        <button
          type="button"
          onClick={onDismiss}
          className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:px-3 niuu:py-1.5 niuu:text-sm niuu:text-text-secondary niuu:hover:bg-bg-tertiary"
        >
          {accepted ? 'Close' : 'Cancel'}
        </button>
        {!accepted && (
          <button
            type="button"
            onClick={() => void handleForward()}
            disabled={!target || sending}
            className="niuu:rounded-md niuu:border niuu:border-brand/40 niuu:bg-brand/10 niuu:px-3 niuu:py-1.5 niuu:text-sm niuu:text-brand niuu:hover:bg-brand/15 niuu:disabled:cursor-not-allowed niuu:disabled:opacity-50"
          >
            {sending ? 'Forwarding…' : 'Forward'}
          </button>
        )}
      </div>
    </div>
  );
}

/** One id can appear on two hosts, so a row is keyed by host and id. */
function rowKey(session: Session): string {
  return `${session.clusterId}:${session.id}`;
}

export function ForwardMessageDialog({ open, onOpenChange, ...form }: ForwardMessageDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title="Forward to session"
        description={`Send this message from ${form.currentSessionName} to another session.`}
      >
        <ForwardMessageForm {...form} onDismiss={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}
