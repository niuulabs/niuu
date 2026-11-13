import { useId, useState, type ReactNode } from 'react';
import { Link, useParams } from '@tanstack/react-router';
import { useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { ErrorState, LoadingState } from '@niuulabs/ui';
import type { ForgeProject, IVolundrService } from '@niuulabs/plugin-volundr';
import { MEMORY_IMPORTS_SERVICE_KEY, type IMemoryImportsService } from '@niuulabs/plugin-ting';
import { useForgeProjects, PROJECTS_QUERY_KEY } from '../application/useProjects';
import { canManageMembers, useProjectMembers, useViewer } from '../application/useProjectMembers';
import { useTeamIntegrations } from '../application/useProjectIntegrations';
import {
  mountNameFor,
  parsePrefixes,
  projectMemoryKey,
  useMemoryImports,
  useProjectMemoryMounts,
  useRegisteredMemories,
  useTrackerProjects,
} from '../application/useProjectMemory';

const BOX =
  'niuu:flex niuu:flex-col niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary';
const ROW =
  'niuu:flex niuu:items-center niuu:justify-between niuu:gap-3 niuu:px-4 niuu:py-3.5 niuu:text-text-primary';
const CHECK = 'niuu:h-5 niuu:w-5 niuu:flex-none niuu:accent-brand-300';
const INPUT =
  'niuu:min-h-12 niuu:w-full niuu:rounded-lg niuu:border niuu:border-border niuu:bg-bg-secondary niuu:px-3.5 niuu:font-mono niuu:text-sm niuu:text-text-primary';
const QUIET = 'niuu:m-0 niuu:text-sm niuu:text-text-secondary';
const MUTED = 'niuu:text-sm niuu:text-text-muted';
const SAVE =
  'niuu:min-h-11 niuu:cursor-pointer niuu:rounded-lg niuu:border-0 niuu:bg-brand-300 niuu:px-5 niuu:text-sm niuu:font-medium niuu:text-bg-primary niuu:disabled:cursor-default niuu:disabled:opacity-50';

function Section({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section className="niuu:flex niuu:flex-col niuu:gap-3" aria-label={title}>
      <h2 className="niuu:m-0 niuu:text-base niuu:font-semibold niuu:text-text-primary">{title}</h2>
      {note ? <p className={QUIET}>{note}</p> : null}
      {children}
    </section>
  );
}

function describe(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

function MemorySettings({ project, owner }: { project: ForgeProject; owner: boolean }) {
  const volundr = useService<IVolundrService>('volundr');
  const importsService = useService<IMemoryImportsService>(MEMORY_IMPORTS_SERVICE_KEY);
  const queryClient = useQueryClient();
  const ids = useId();
  const mounts = useProjectMemoryMounts(project);
  const registered = useRegisteredMemories();
  const integrations = useTeamIntegrations(project);
  const linear = (integrations.data ?? []).find((connection) => connection.slug === 'linear');
  const imports = useMemoryImports(project.id, owner);
  const existing = imports.data?.find((item) => item.connector === 'linear');
  const trackers = useTrackerProjects(owner && Boolean(linear));

  const mountedIds = (mounts.data ?? []).map((mount) => mount.registry_entry_id);
  const [reads, setReads] = useState<Set<string> | null>(null);
  const [prefixes, setPrefixes] = useState((project.memory_write_prefixes ?? []).join(', '));
  // What the person changed; until then, what Forge and Ting hold.
  const [importChoice, setImportOn] = useState<boolean | null>(null);
  const importOn = importChoice ?? Boolean(existing);
  const [trackerProject, setTrackerProject] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  if (mounts.isLoading || registered.isLoading) return <LoadingState label="Loading memory…" />;
  if (mounts.error ?? registered.error)
    return (
      <ErrorState
        title="Could not load the project's memory"
        message={describe(mounts.error ?? registered.error)}
      />
    );

  const chosen = reads ?? new Set(mountedIds);
  const entries = registered.data ?? [];
  // A mount whose entry is no longer listed is still shown, so nothing is hidden.
  const unlisted = (mounts.data ?? []).filter(
    (mount) => !entries.some((entry) => entry.id === mount.registry_entry_id),
  );
  const firstTracker = (trackers.data ?? [])[0]?.id ?? '';
  const pickedTracker = trackerProject || firstTracker;
  const trackerName = (id: string) =>
    (trackers.data ?? []).find((candidate) => candidate.id === id)?.name ?? id;

  async function save() {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      for (const mount of mounts.data ?? []) {
        if (!chosen.has(mount.registry_entry_id))
          await volundr.removeProjectMemoryMount(project, mount.name);
      }
      for (const entry of entries) {
        if (chosen.has(entry.id) && !mountedIds.includes(entry.id))
          await volundr.addProjectMemoryMount(project, {
            name: mountNameFor(entry.name),
            registryEntryId: entry.id,
            access: 'read',
          });
      }
      const wanted = parsePrefixes(prefixes);
      if (wanted.join(',') !== (project.memory_write_prefixes ?? []).join(','))
        await volundr.updateProject(project, { memory_write_prefixes: wanted });
      if (existing && !importOn) await importsService.remove(existing.id);
      if (!existing && importOn && linear)
        await importsService.create({
          projectId: project.id,
          connectionId: linear.id,
          trackerProjectId: pickedTracker,
          compile: true,
        });
      setSaved(true);
    } catch (cause) {
      setError(describe(cause));
    } finally {
      setBusy(false);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: projectMemoryKey(project.id) }),
        queryClient.invalidateQueries({ queryKey: PROJECTS_QUERY_KEY }),
      ]);
    }
  }

  function toggle(entryId: string, on: boolean) {
    const next = new Set(chosen);
    if (on) next.add(entryId);
    else next.delete(entryId);
    setReads(next);
    setSaved(false);
  }

  const locked = !owner || busy;
  return (
    <>
      <Section
        title="What this project also reads"
        note="Everyone on the project, and their agents, can find what's in these."
      >
        <div className={BOX}>
          <label className={ROW}>
            <span>
              The org&apos;s memory <span className={MUTED}>· handbook, people, customers</span>
            </span>
            <input type="checkbox" className={CHECK} checked disabled />
          </label>
          {entries.map((entry) => (
            <label key={entry.id} className={`${ROW} niuu:border-t niuu:border-border-subtle`}>
              <span>
                {entry.name}
                {entry.desc ? <span className={MUTED}> · {entry.desc}</span> : null}
              </span>
              <input
                type="checkbox"
                className={CHECK}
                checked={chosen.has(entry.id)}
                disabled={locked}
                onChange={(event) => toggle(entry.id, event.target.checked)}
              />
            </label>
          ))}
          {unlisted.map((mount) => (
            <label key={mount.name} className={`${ROW} niuu:border-t niuu:border-border-subtle`}>
              <span>{mount.name}</span>
              <input
                type="checkbox"
                className={CHECK}
                checked={chosen.has(mount.registry_entry_id)}
                disabled={locked}
                onChange={(event) => toggle(mount.registry_entry_id, event.target.checked)}
              />
            </label>
          ))}
        </div>
      </Section>

      <Section
        title="Where agents may write without asking"
        note="Anything else they want to add comes to you to keep or discard."
      >
        <label htmlFor={`${ids}-prefixes`} className="niuu:sr-only">
          Folders agents may write to
        </label>
        <input
          id={`${ids}-prefixes`}
          type="text"
          className={INPUT}
          value={prefixes}
          placeholder="decisions/, runbooks/"
          disabled={locked}
          onChange={(event) => {
            setPrefixes(event.target.value);
            setSaved(false);
          }}
        />
      </Section>

      <Section title="Bring in tickets">
        {linear ? (
          <div className={`${BOX} niuu:gap-3 niuu:px-4 niuu:py-3.5`}>
            <label className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3 niuu:text-text-primary">
              <span>
                Linear
                {existing ? ` · ${trackerName(existing.trackerProjectId)}` : ''}
                <span className={`niuu:block ${MUTED}`}>
                  New and changed tickets are added, and summarised into the project&apos;s pages.
                </span>
              </span>
              <input
                type="checkbox"
                className={CHECK}
                checked={importOn}
                disabled={locked || imports.isLoading}
                onChange={(event) => {
                  setImportOn(event.target.checked);
                  setSaved(false);
                }}
              />
            </label>
            {importOn && !existing ? (
              (trackers.data ?? []).length > 0 ? (
                <select
                  aria-label="Linear project"
                  className={INPUT}
                  value={pickedTracker}
                  disabled={locked}
                  onChange={(event) => setTrackerProject(event.target.value)}
                >
                  {(trackers.data ?? []).map((candidate) => (
                    <option key={candidate.id} value={candidate.id}>
                      {candidate.name}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  aria-label="Linear project"
                  type="text"
                  className={INPUT}
                  placeholder="The Linear project's id"
                  value={trackerProject}
                  disabled={locked}
                  onChange={(event) => setTrackerProject(event.target.value)}
                />
              )
            ) : null}
            {existing?.lastError ? (
              <p className="niuu:m-0 niuu:text-sm niuu:text-critical-fg">{existing.lastError}</p>
            ) : null}
          </div>
        ) : (
          <p className={QUIET}>
            Connect Linear in the project&apos;s integrations to bring in its tickets.
          </p>
        )}
      </Section>

      {owner ? (
        <div className="niuu:flex niuu:items-center niuu:gap-3">
          <button
            type="button"
            className={SAVE}
            disabled={busy || (importOn && !existing && !pickedTracker)}
            onClick={() => void save()}
          >
            {busy ? 'Saving…' : 'Save'}
          </button>
          {saved ? (
            <span className={MUTED} role="status">
              Saved.
            </span>
          ) : null}
        </div>
      ) : (
        <p className={QUIET}>Only the project&apos;s owners change these.</p>
      )}
      {error ? (
        <p className="niuu:m-0 niuu:text-sm niuu:text-critical-fg" role="alert">
          {error}
        </p>
      ) : null}
    </>
  );
}

/** A project's memory settings: what else it reads, where agents write, tickets it brings in. */
export function ProjectMemoryPage() {
  const { projectId } = useParams({ strict: false }) as { projectId: string };
  const projects = useForgeProjects();
  const project = projects.data?.find((entry) => entry.id === projectId) ?? null;
  const viewer = useViewer();
  const members = useProjectMembers(
    project ?? ({ id: projectId, name: '', slug: '', status: 'active' } as ForgeProject),
  );

  if (projects.isLoading) return <LoadingState label="Loading project…" />;
  if (projects.error)
    return <ErrorState title="Could not load the project" message={describe(projects.error)} />;
  if (!project)
    return (
      <ErrorState
        title="No such project"
        message="This project does not exist, or no Forge you can reach holds it."
      />
    );
  const owner = canManageMembers(members.data ?? [], viewer.data);

  return (
    <div
      className="niuu:h-full niuu:overflow-auto niuu:bg-bg-primary niuu:px-6 niuu:py-16"
      data-testid="project-memory-page"
    >
      <div className="niuu:mx-auto niuu:flex niuu:w-full niuu:max-w-2xl niuu:flex-col niuu:gap-8">
        <div>
          <Link
            to="/realms/projects/$projectId"
            params={{ projectId } as never}
            className="niuu:text-sm niuu:text-text-secondary"
          >
            {project.name} · Settings
          </Link>
          <h1 className="niuu:m-0 niuu:mt-1 niuu:text-2xl niuu:font-semibold niuu:text-text-primary">
            Memory
          </h1>
        </div>
        {members.isLoading || viewer.isLoading ? (
          <LoadingState label="Loading…" />
        ) : (
          <MemorySettings project={project} owner={owner} />
        )}
      </div>
    </div>
  );
}
