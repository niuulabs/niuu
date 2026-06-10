import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import {
  Dialog,
  DialogContent,
  EmptyState,
  ErrorState,
  LiveBadge,
  LoadingState,
  cn,
  relTime,
} from '@niuulabs/ui';
import { FolderGit2 } from 'lucide-react';
import { CliBadge } from './atoms/CliBadge';
import {
  isExternalSessionsUnavailableError,
  useExternalSessions,
} from './hooks/useExternalSessions';
import type { ExternalSession, VolundrSession } from '../models/volundr.model';
import type { IVolundrService } from '../ports/IVolundrService';

export interface ImportExternalSessionsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called after a session was imported and caches were invalidated. */
  onImported?: (session: VolundrSession) => void | Promise<void>;
}

export function externalSessionKey(session: ExternalSession): string {
  return `${session.provider}:${session.externalId}`;
}

export function externalSessionTitle(session: ExternalSession): string {
  return session.title.trim() || session.externalId;
}

export function externalSessionActivityTs(session: ExternalSession): number | null {
  const value = session.updatedAt ?? session.createdAt;
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

function ExternalSessionRow({
  session,
  busy,
  error,
  onImport,
}: {
  session: ExternalSession;
  busy: boolean;
  error?: string;
  onImport: () => void;
}) {
  const activityTs = externalSessionActivityTs(session);
  const importable = session.workspaceExists && session.workspaceAllowed;
  const importTitle = !session.workspaceExists
    ? 'Cannot import: workspace directory no longer exists'
    : !session.workspaceAllowed
      ? 'Cannot import: workspace is outside the allowed mount prefixes'
      : `Import ${externalSessionTitle(session)}`;

  return (
    <li
      className="niuu:flex niuu:items-start niuu:gap-3 niuu:border-b niuu:border-border-subtle niuu:px-3 niuu:py-2.5 niuu:last:border-b-0"
      data-testid={`external-session-row-${session.externalId}`}
    >
      <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col niuu:gap-1">
        <div className="niuu:flex niuu:min-w-0 niuu:items-center niuu:gap-2">
          <CliBadge cli={session.harness} />
          <span className="niuu:truncate niuu:font-mono niuu:text-[13px] niuu:font-medium niuu:text-text-primary">
            {externalSessionTitle(session)}
          </span>
          {session.live ? (
            <span
              className="niuu:flex-shrink-0"
              data-testid={`external-session-live-${session.externalId}`}
            >
              <LiveBadge label="LIVE" />
            </span>
          ) : null}
        </div>
        <div className="niuu:flex niuu:min-w-0 niuu:flex-wrap niuu:items-center niuu:gap-x-3 niuu:gap-y-0.5 niuu:font-mono niuu:text-[10px] niuu:text-text-muted">
          <span
            className="niuu:flex niuu:min-w-0 niuu:items-center niuu:gap-1.5"
            title={session.workspacePath}
          >
            <FolderGit2 className="niuu:h-3 niuu:w-3 niuu:flex-shrink-0 niuu:text-text-faint" />
            <span className="niuu:truncate">{session.workspacePath}</span>
          </span>
          {activityTs !== null ? <span>{relTime(activityTs)}</span> : null}
          {session.model ? <span className="niuu:text-text-faint">{session.model}</span> : null}
        </div>
        {!session.workspaceExists ? (
          <span
            className="niuu:font-mono niuu:text-[10px] niuu:text-text-faint"
            data-testid={`external-session-missing-workspace-${session.externalId}`}
          >
            workspace directory no longer exists
          </span>
        ) : null}
        {session.workspaceExists && !session.workspaceAllowed ? (
          <span
            className="niuu:font-mono niuu:text-[10px] niuu:text-text-faint"
            data-testid={`external-session-workspace-not-allowed-${session.externalId}`}
          >
            workspace is outside the allowed mount prefixes
          </span>
        ) : null}
        {error ? (
          <span
            className="niuu:font-mono niuu:text-[10px] niuu:text-critical"
            data-testid={`external-session-import-error-${session.externalId}`}
          >
            {error}
          </span>
        ) : null}
      </div>
      {session.importedSessionId ? (
        <span
          className="niuu:mt-0.5 niuu:flex-shrink-0 niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-tertiary niuu:px-2.5 niuu:py-1 niuu:font-mono niuu:text-[10px] niuu:text-text-muted"
          data-testid={`external-session-imported-${session.externalId}`}
        >
          Imported
        </span>
      ) : (
        <button
          type="button"
          onClick={onImport}
          disabled={busy || !importable}
          title={importTitle}
          className={cn(
            'niuu:mt-0.5 niuu:flex-shrink-0 niuu:rounded-md niuu:border niuu:px-2.5 niuu:py-1 niuu:font-mono niuu:text-[10px] niuu:transition-colors',
            importable
              ? 'niuu:border-brand/40 niuu:bg-brand/10 niuu:text-brand niuu:hover:bg-brand/15'
              : 'niuu:border-border-subtle niuu:text-text-faint',
            'niuu:disabled:cursor-not-allowed niuu:disabled:opacity-50',
          )}
          data-testid={`external-session-import-${session.externalId}`}
        >
          {busy ? 'Importing…' : 'Import'}
        </button>
      )}
    </li>
  );
}

export function ImportExternalSessionsDialog({
  open,
  onOpenChange,
  onImported,
}: ImportExternalSessionsDialogProps) {
  const volundr = useService<IVolundrService>('volundr');
  const queryClient = useQueryClient();
  const externalQuery = useExternalSessions();
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [importErrors, setImportErrors] = useState<Record<string, string>>({});

  const unavailable = isExternalSessionsUnavailableError(externalQuery.error);
  const sessions = externalQuery.data ?? [];

  async function handleImport(session: ExternalSession) {
    if (busyKey) return;
    const key = externalSessionKey(session);
    setBusyKey(key);
    setImportErrors((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
    try {
      const imported = await volundr.importExternalSession(session.provider, session.externalId);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['volundr', 'domain-sessions'] }),
        queryClient.invalidateQueries({ queryKey: ['volundr', 'history'] }),
      ]);
      await externalQuery.refetch();
      await onImported?.(imported);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Import failed';
      setImportErrors((current) => ({ ...current, [key]: message }));
    } finally {
      setBusyKey(null);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title="Import CLI Sessions"
        description="Claude Code and Codex sessions discovered on the host can be imported as Völundr sessions."
      >
        <div data-testid="import-external-sessions-panel">
          {externalQuery.isLoading && <LoadingState label="Discovering external sessions…" />}
          {externalQuery.isError && unavailable && (
            <EmptyState
              title="Discovery unavailable"
              description="External session discovery is not enabled on this server."
            />
          )}
          {externalQuery.isError && !unavailable && (
            <ErrorState
              title="Failed to discover external sessions"
              message={
                externalQuery.error instanceof Error ? externalQuery.error.message : 'Unknown error'
              }
            />
          )}
          {externalQuery.isSuccess && sessions.length === 0 && (
            <EmptyState
              title="No external sessions found"
              description="No Claude Code or Codex sessions were discovered on the host."
            />
          )}
          {externalQuery.isSuccess && sessions.length > 0 && (
            <ul
              className="niuu:max-h-96 niuu:overflow-y-auto niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-tertiary"
              data-testid="external-session-list"
            >
              {sessions.map((session) => {
                const key = externalSessionKey(session);
                return (
                  <ExternalSessionRow
                    key={key}
                    session={session}
                    busy={busyKey === key}
                    error={importErrors[key]}
                    onImport={() => void handleImport(session)}
                  />
                );
              })}
            </ul>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
