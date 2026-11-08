import { useCallback, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IVolundrService } from '../../ports/IVolundrService';
import { SessionReadStateConflictError, type Session } from '../../domain/session';
import type { SessionReadStateChange } from '../../models/volundr.model';
import { errorText } from '../errorText';

const SESSION_QUERY_ROOTS = [
  ['volundr', 'domain-sessions'],
  ['volundr', 'domain-session'],
  ['volundr', 'sessions'],
] as const;

export interface UseSessionRead {
  /** Marks the session's latest reply read. Resolves false, with `error` set, when it fails. */
  markRead(session: Session): Promise<boolean>;
  /** Marks the session unread. Resolves false, with `error` set, when it fails. */
  markUnread(session: Session): Promise<boolean>;
  /**
   * Marks every given session read, each one independently. Resolves false when any failed;
   * `error` then says how many and why.
   */
  markAllRead(sessions: readonly Session[]): Promise<boolean>;
  /** Sessions with a read or unread action in flight. */
  pendingIds: ReadonlySet<string>;
  /** The last failure, including a revision conflict; null once cleared or after a success. */
  error: string | null;
  clearError(): void;
}

function missingReadState(session: Session): Error {
  return new Error(
    `Forge did not report a read state for ${session.name || session.id}, so it cannot be marked read or unread.`,
  );
}

/**
 * The caller's read and unread actions for Forge sessions. The change carries the revision the
 * caller saw, so a newer action elsewhere is refused (a conflict), never overwritten. A
 * conflict refreshes the session lists and is reported; nothing is retried on its own.
 */
export function useSessionRead(): UseSessionRead {
  const volundr = useService<IVolundrService>('volundr');
  const queryClient = useQueryClient();
  const [pendingIds, setPendingIds] = useState<ReadonlySet<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  const refreshSessions = useCallback(
    () =>
      Promise.all(
        SESSION_QUERY_ROOTS.map((queryKey) => queryClient.invalidateQueries({ queryKey })),
      ),
    [queryClient],
  );

  const setPending = useCallback((id: string, pending: boolean) => {
    setPendingIds((current) => {
      const next = new Set(current);
      if (pending) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  // Throws; the public actions turn the failure into `error`.
  const change = useCallback(
    async (session: Session, state: SessionReadStateChange['state']) => {
      const readState = session.readState;
      if (!readState) throw missingReadState(session);
      setPending(session.id, true);
      try {
        await volundr.changeSessionReadState(session.id, {
          state,
          expectedRevision: readState.revision,
          throughSeq: readState.latestOutputSeq,
        });
      } catch (failure) {
        // Show what won, so the next click decides from the current state.
        if (failure instanceof SessionReadStateConflictError) await refreshSessions();
        throw failure;
      } finally {
        setPending(session.id, false);
      }
      await refreshSessions();
    },
    [volundr, refreshSessions, setPending],
  );

  const run = useCallback(
    async (session: Session, state: SessionReadStateChange['state']) => {
      setError(null);
      try {
        await change(session, state);
        return true;
      } catch (failure) {
        setError(errorText(failure, `Could not mark ${session.name || session.id} ${state}`));
        return false;
      }
    },
    [change],
  );

  const markRead = useCallback(
    async (session: Session) => {
      // Nothing newer than what was read: there is no action to take.
      if (session.readState && !session.readState.isUnread) return true;
      return run(session, 'read');
    },
    [run],
  );

  const markUnread = useCallback(
    async (session: Session) => {
      if (session.readState?.manuallyUnread) return true;
      return run(session, 'unread');
    },
    [run],
  );

  const markAllRead = useCallback(
    async (sessions: readonly Session[]) => {
      setError(null);
      const unread = sessions.filter((session) => session.readState?.isUnread === true);
      const results = await Promise.allSettled(unread.map((session) => change(session, 'read')));
      const failures = results.filter(
        (result): result is PromiseRejectedResult => result.status === 'rejected',
      );
      if (failures.length === 0) return true;
      setError(
        `${failures.length} of ${unread.length} sessions could not be marked read: ${errorText(failures[0]?.reason, 'unknown error')}`,
      );
      return false;
    },
    [change],
  );

  const clearError = useCallback(() => setError(null), []);

  return { markRead, markUnread, markAllRead, pendingIds, error, clearError };
}
