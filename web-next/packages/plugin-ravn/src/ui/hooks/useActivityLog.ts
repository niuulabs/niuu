import { useMemo } from 'react';
import { useSessions } from './useSessions';
import { useTriggers } from './useTriggers';
import type { ActivityLogEntry } from '../../domain/activityLog';
import type { Session } from '../../domain/session';
import type { Trigger } from '../../domain/trigger';

const ACTIVITY_LOG_ROWS = 9;

function outputEventFor(session: Session): string {
  switch (session.personaRole) {
    case 'review':
      return 'review.verdict';
    case 'observe':
      return 'incident.report';
    case 'knowledge':
      return 'mimir.write';
    case 'qa':
      return 'qa.completed';
    case 'build':
      return 'code.changed';
    case 'report':
      return 'report.ready';
    case 'coord':
      return 'workflow.progress';
    case 'investigate':
      return 'investigation.report';
    default:
      return 'session.output';
  }
}

function sessionMessage(s: Session): string {
  const title = s.title ?? `working session ${s.id.slice(0, 6)}`;
  const iterations = s.messageCount ?? 0;
  return `iter ${iterations} — ${title.toLowerCase()}`;
}

function emitMessage(s: Session): string {
  const title = s.title ?? 'completed';
  return `${outputEventFor(s)} · ${title.toLowerCase()}`;
}

function sessionToEntry(s: Session): ActivityLogEntry {
  const kind = s.status === 'running' ? 'session' : 'emit';
  return {
    id: `session-${s.id}`,
    ts: s.createdAt,
    kind,
    ravnId: s.personaName,
    message: kind === 'session' ? sessionMessage(s) : emitMessage(s),
  };
}

function triggerMessage(t: Trigger): string {
  switch (t.kind) {
    case 'cron':
      return `cron.tick · ${t.spec}`;
    case 'event':
      return `${t.spec} · dispatch ${t.personaName}`;
    case 'manual':
      return `manual.run · ${t.spec}`;
    case 'webhook':
      return `webhook.hit · ${t.spec}`;
    default:
      return `${t.kind} · ${t.spec}`;
  }
}

function triggerToEntry(t: Trigger): ActivityLogEntry {
  return {
    id: `trigger-${t.id}`,
    ts: t.lastFiredAt ?? t.createdAt,
    kind: 'trigger',
    ravnId: t.personaName,
    message: triggerMessage(t),
  };
}

/**
 * Derives recent fleet activity from sessions and triggers.
 * Returns up to 9 entries sorted by timestamp descending.
 */
export function useActivityLog(): {
  data: ActivityLogEntry[] | undefined;
  isLoading: boolean;
  isError: boolean;
} {
  const sessions = useSessions();
  const triggers = useTriggers();

  const data = useMemo((): ActivityLogEntry[] | undefined => {
    if (!sessions.data) return undefined;

    const entries: ActivityLogEntry[] = sessions.data.map(sessionToEntry);

    if (triggers.data) {
      for (const t of triggers.data) {
        entries.push(triggerToEntry(t));
      }
    }

    return entries.sort((a, b) => b.ts.localeCompare(a.ts)).slice(0, ACTIVITY_LOG_ROWS);
  }, [sessions.data, triggers.data]);

  return {
    data,
    isLoading: sessions.isLoading || triggers.isLoading,
    isError: sessions.isError || triggers.isError,
  };
}
