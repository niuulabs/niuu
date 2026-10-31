import { useId, useState, type FormEvent } from 'react';
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { Popover, PopoverContent, PopoverTrigger } from '@niuulabs/ui';
import { FolderInput } from 'lucide-react';
import type { IVolundrService } from '../ports/IVolundrService';
import type { ISessionStore } from '../ports/ISessionStore';
import type { Session } from '../domain/session';
import { readForgeSource } from './hooks/readForgeSource';
import { forgeErrorMessage } from './quickLaunchModel';
import './RenameSession.css';
import './AssignSessionProject.css';

export function AssignSessionProject({
  sessionId,
  name,
  instanceId,
  disabled = false,
}: {
  sessionId: string;
  name: string;
  instanceId?: string;
  disabled?: boolean;
}) {
  const service = useService<IVolundrService>('volundr');
  const store = useService<ISessionStore>('sessionStore');
  const cache = useQueryClient();
  const inputId = useId();
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const targets = useQuery({
    queryKey: ['volundr', 'targets', 'project-assignment'],
    queryFn: () => service.getTargets(),
    enabled: open,
    retry: false,
  });
  const membership = useQuery({
    queryKey: ['volundr', 'session-project', instanceId, sessionId],
    queryFn: ({ signal }) =>
      readForgeSource(
        (sourceSignal) =>
          service.getSessionProject(sessionId, { instanceId, signal: sourceSignal }),
        signal,
        store.listRequestTimeoutMs,
      ),
    enabled: open,
    retry: false,
    staleTime: 0,
  });
  const hosts = targets.data ?? [];
  const projects = useQueries({
    queries: hosts.map((host) => ({
      queryKey: ['volundr', 'projects', 'source', host.id],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        readForgeSource(
          (sourceSignal) => service.getProjects({ instanceId: host.id, signal: sourceSignal }),
          signal,
          store.listRequestTimeoutMs,
        ),
      enabled: open,
      retry: false,
    })),
  });
  const choices = projects
    .flatMap((result, index) =>
      (result.data ?? [])
        .filter((project) => project.status === 'active')
        .map((project) => ({
          project,
          host: hosts[index]!,
          key: `${hosts[index]!.id}:${project.id}`,
        })),
    )
    .sort(
      (a, b) =>
        a.project.name.localeCompare(b.project.name) || a.host.name.localeCompare(b.host.name),
    );
  const choice = choices.find((item) => item.key === selected);
  const lockedCoordinator = membership.data?.role === 'coordinator';
  const changed = choice && choice.project.id !== membership.data?.projectId;
  const unavailable = projects.flatMap((result, index) =>
    result.error ? [hosts[index]!.name] : [],
  );
  const loading =
    targets.isPending || membership.isPending || projects.some((result) => result.isPending);

  async function reload() {
    setError('');
    await Promise.all([
      targets.refetch(),
      membership.refetch(),
      ...projects.map((query) => query.refetch()),
    ]);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (
      !choice ||
      !membership.data ||
      !changed ||
      saving ||
      lockedCoordinator ||
      membership.isError
    )
      return;
    setSaving(true);
    setError('');
    try {
      const assigned = await service.assignSessionProject(
        sessionId,
        {
          projectId: choice.project.id,
          projectInstanceId: choice.host.id,
          expectedRevision: membership.data.revision,
        },
        { instanceId },
      );
      const coordination = {
        projectId: assigned.projectId!,
        role: assigned.role ?? 'worker',
        parent: null,
      };
      await cache.cancelQueries({ queryKey: ['volundr', 'domain-sessions'] });
      cache.setQueriesData<Session[]>({ queryKey: ['volundr', 'domain-sessions'] }, (sessions) =>
        sessions?.map((session) =>
          session.id === sessionId && (!instanceId || session.clusterId === instanceId)
            ? { ...session, coordination }
            : session,
        ),
      );
      cache.setQueryData(['volundr', 'session-project', instanceId, sessionId], assigned);
      setOpen(false);
      // Refresh other views independently; an offline host must not delay a successful save.
      void Promise.all(
        [
          ['volundr', 'session-project'],
          ['volundr', 'projects'],
          ['volundr', 'domain-sessions'],
          ['volundr', 'domain-session', sessionId],
          ['volundr', 'raw-session', sessionId],
          ['volundr', 'session-list'],
        ].map((queryKey) => cache.invalidateQueries({ queryKey })),
      );
    } catch (cause) {
      setError(forgeErrorMessage(cause));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (saving) return;
        if (next) {
          setSelected('');
          setError('');
        }
        setOpen(next);
      }}
    >
      <PopoverTrigger asChild>
        <button
          type="button"
          className="forge-session-rename"
          title="Assign or move to project"
          aria-label={`Assign project for ${name}`}
          disabled={disabled || saving}
        >
          <FolderInput size={15} />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="forge-session-rename-popover forge-session-project-popover"
      >
        <form onSubmit={(event) => void save(event)} aria-label="Assign session project">
          <label htmlFor={inputId}>Project</label>
          <select
            id={inputId}
            value={selected}
            onChange={(event) => {
              setSelected(event.target.value);
              setError('');
            }}
            disabled={saving || lockedCoordinator}
          >
            <option value="">Choose a project…</option>
            {choices.map(({ key, project, host }) => (
              <option key={key} value={key}>
                {project.name} · {host.name}
                {project.id === membership.data?.projectId ? ' (current)' : ''}
              </option>
            ))}
          </select>
          <p>The session stays on its current host and keeps its workspace and conversation.</p>
          {membership.data?.projectId && !lockedCoordinator && (
            <p>Moving to another project removes this session’s current coordinator link.</p>
          )}
          {lockedCoordinator && (
            <p>Create a coordinator in the destination project and archive this one.</p>
          )}
          {loading && <p role="status">Loading projects and session…</p>}
          {!loading && choices.length === 0 && (
            <p>No active projects are available. Connect a project in Forge first.</p>
          )}
          {unavailable.length > 0 && (
            <p role="alert">
              Could not load projects from {unavailable.join(', ')}. Other hosts remain available.
            </p>
          )}
          {(error || membership.error || targets.error) && (
            <p role="alert">{error || forgeErrorMessage(membership.error || targets.error)}</p>
          )}
          <footer>
            {(error || unavailable.length > 0 || membership.error || targets.error) && (
              <button type="button" disabled={saving} onClick={() => void reload()}>
                Reload
              </button>
            )}
            <button type="button" disabled={saving} onClick={() => setOpen(false)}>
              Cancel
            </button>
            <button
              type="submit"
              disabled={
                saving || !changed || lockedCoordinator || membership.isError || !membership.data
              }
            >
              {saving ? 'Saving…' : membership.data?.projectId ? 'Move' : 'Assign'}
            </button>
          </footer>
        </form>
      </PopoverContent>
    </Popover>
  );
}
