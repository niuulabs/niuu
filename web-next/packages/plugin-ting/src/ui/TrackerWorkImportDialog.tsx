import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import {
  BranchSelect,
  EmptyState,
  ErrorState,
  LoadingState,
  Modal,
  RepoSelect,
  type RepoRecord,
} from '@niuulabs/ui';
import type {
  DispatchCluster,
  IDispatchBus,
  ITrackerBrowserService,
  Saga,
  TrackerProject,
} from '../ports';
import type { WorkSummary } from '../domain/work';

type RepoCatalogService = {
  getRepos(): Promise<RepoRecord[]>;
  getBranches(repoUrl: string): Promise<string[]>;
};

function projectKey(project: TrackerProject): string {
  return `${project.trackerConnectionId ?? ''}:${project.id}`;
}

export function TrackerWorkImportDialog({
  open,
  onOpenChange,
  onImported,
  connectedProjects,
  onOpenProject,
  openProjectLabel = 'Open connected project',
  forgeProjectId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onImported: (saga: Saga) => void;
  connectedProjects: WorkSummary[];
  /** What happens to a tracker project Work already has: opened in Work, or used where asked. */
  onOpenProject: (project: WorkSummary) => void;
  openProjectLabel?: string;
  /** Forge project the tracker joins; omitted, it stands on its own. */
  forgeProjectId?: string;
}) {
  const tracker = useService<ITrackerBrowserService>('ting.tracker');
  const repos = useService<RepoCatalogService>('niuu.repos');
  const dispatch = useService<IDispatchBus>('ting.dispatch');
  const projectsQuery = useQuery({
    queryKey: ['ting', 'tracker', 'projects'],
    queryFn: () => tracker.listProjects(),
    enabled: open,
  });
  const reposQuery = useQuery({
    queryKey: ['niuu', 'repos'],
    queryFn: () => repos.getRepos(),
    enabled: open,
  });
  const targetsQuery = useQuery({
    queryKey: ['ting', 'dispatch', 'targets'],
    queryFn: () => dispatch.getClusters(),
    enabled: open,
  });
  const [projectSourceKey, setProjectSourceKey] = useState('');
  const [repo, setRepo] = useState('');
  const [branch, setBranch] = useState('');
  const [targetId, setTargetId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const projects = projectsQuery.data ?? [];
  const catalog = reposQuery.data ?? [];
  if (open && !projectSourceKey && projects[0]) {
    setProjectSourceKey(projectKey(projects[0]));
  }
  const selectedProject = projectSourceKey
    ? (projects.find((project) => projectKey(project) === projectSourceKey) ?? null)
    : (projects[0] ?? null);
  const selectedProjectMissing = Boolean(projectSourceKey && !selectedProject);
  const enabledTargets = useMemo(
    () => (targetsQuery.data ?? []).filter((target) => target.enabled),
    [targetsQuery.data],
  );
  const existingProject = selectedProject
    ? (connectedProjects.find(
        (project) =>
          project.source?.projectId === selectedProject.id &&
          project.source.connectionId === (selectedProject.trackerConnectionId ?? ''),
      ) ?? null)
    : null;
  const selectedTarget = enabledTargets.find(
    (target) => (target.instanceId ?? target.connectionId) === targetId,
  );
  const selectedTargetMissing = Boolean(targetId && !selectedTarget);
  const canImport = Boolean(
    projectsQuery.isSuccess &&
    reposQuery.isSuccess &&
    targetsQuery.isSuccess &&
    selectedProject &&
    !selectedProjectMissing &&
    !existingProject &&
    (!repo.trim() || branch.trim()) &&
    !selectedTargetMissing &&
    !busy,
  );

  function reset() {
    setProjectSourceKey('');
    setRepo('');
    setBranch('');
    setTargetId('');
    setBusy(false);
    setError(null);
  }

  function setOpen(next: boolean) {
    if (!next && busy) return;
    onOpenChange(next);
    if (!next) reset();
  }

  async function importProject() {
    if (!selectedProject || !canImport) return;
    setBusy(true);
    setError(null);
    try {
      const target = selectedTarget;
      const saga = await tracker.importProject(
        selectedProject.id,
        repo ? [repo] : [],
        branch,
        target?.instanceId ?? undefined,
        {
          repoRefs: repo ? [{ repo, branch }] : [],
          trackerConnectionId: selectedProject.trackerConnectionId,
          ...(forgeProjectId ? { forgeProjectId } : {}),
          target: target
            ? { mode: 'instance', instanceId: target.instanceId ?? target.connectionId }
            : { mode: 'default' },
        },
      );
      onOpenChange(false);
      reset();
      onImported(saga);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not import this tracker project');
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onOpenChange={setOpen}
      title="Bring in a tracker project"
      description="Bring its tickets in, with a repository if the work changes code. The workflows they run are set on the board. Importing starts nothing."
      className="niuu:max-w-[900px]"
      actions={[
        { label: 'Cancel', variant: 'secondary', disabled: busy },
        {
          label: busy ? 'Importing…' : 'Import project',
          variant: 'primary',
          disabled: !canImport,
          closes: false,
          onClick: () => void importProject(),
        },
      ]}
    >
      {projectsQuery.isLoading || reposQuery.isLoading || targetsQuery.isLoading ? (
        <LoadingState label="Loading connected projects…" />
      ) : projectsQuery.isError || reposQuery.isError || targetsQuery.isError ? (
        <ErrorState
          title="Tracker projects could not be loaded"
          message={
            [projectsQuery.error, reposQuery.error, targetsQuery.error].find(Boolean) instanceof
            Error
              ? ([projectsQuery.error, reposQuery.error, targetsQuery.error].find(Boolean) as Error)
                  .message
              : 'Unknown error'
          }
        />
      ) : projects.length === 0 ? (
        <EmptyState
          title="No tracker projects available"
          description="Connect a tracker in Settings, then return here."
        />
      ) : (
        <div className="ting-tracker-import">
          <div className="ting-tracker-import__projects" aria-label="Tracker projects">
            {projects.map((project) => (
              <button
                key={projectKey(project)}
                type="button"
                aria-pressed={Boolean(
                  selectedProject && projectKey(selectedProject) === projectKey(project),
                )}
                onClick={() => {
                  setProjectSourceKey(projectKey(project));
                  setError(null);
                }}
              >
                <strong>{project.name}</strong>
                <span>
                  {project.trackerName || project.trackerType || 'Tracker'} · {project.issueCount}{' '}
                  tickets
                  {connectedProjects.some(
                    (connected) =>
                      connected.source?.projectId === project.id &&
                      connected.source.connectionId === (project.trackerConnectionId ?? ''),
                  )
                    ? ' · connected'
                    : ''}
                </span>
              </button>
            ))}
          </div>
          <div className="ting-tracker-import__setup">
            <div>
              <span className="ting-work-eyebrow">Selected project</span>
              <h3>{selectedProject?.name}</h3>
              <p>
                Choose where its tickets run. You can review eligible tickets before dispatching
                them.
              </p>
            </div>
            {existingProject ? null : (
              <>
                <label>
                  <span>Repository (optional)</span>
                  <RepoSelect
                    repos={catalog}
                    value={repo}
                    valueMode="slug"
                    onChange={(value) => {
                      const selected = catalog.find(
                        (candidate) =>
                          `${candidate.org}/${candidate.name}` === value ||
                          candidate.cloneUrl === value,
                      );
                      setRepo(value);
                      setBranch(selected?.defaultBranch ?? 'main');
                    }}
                    placeholder="Select repository"
                    testId="work-import-repository"
                  />
                </label>
                <label>
                  <span>Branch</span>
                  <BranchSelect
                    loadBranches={repos.getBranches}
                    repos={catalog}
                    selectedRepos={repo ? [repo] : []}
                    value={branch}
                    onChange={setBranch}
                    placeholder="Select branch"
                    testId="work-import-branch"
                  />
                </label>
                <label>
                  <span>Execution target</span>
                  <select value={targetId} onChange={(event) => setTargetId(event.target.value)}>
                    <option value="">Let Niuu choose</option>
                    {enabledTargets.map((target: DispatchCluster) => (
                      <option
                        key={target.instanceId ?? target.connectionId}
                        value={target.instanceId ?? target.connectionId}
                      >
                        {target.name}
                      </option>
                    ))}
                  </select>
                </label>
              </>
            )}
            <p className="ting-tracker-import__effect">
              {existingProject
                ? 'Work already has this tracker project, with its tickets and board.'
                : 'This imports project structure and assignments. Review the resulting ticket list, then start only eligible work explicitly.'}
            </p>
            {existingProject ? (
              <button
                type="button"
                className="ting-tracker-import__existing"
                onClick={() => {
                  setOpen(false);
                  onOpenProject(existingProject);
                }}
              >
                {openProjectLabel}
              </button>
            ) : null}
            {selectedTargetMissing ? (
              <p role="alert" className="ting-work-action-error">
                The selected execution target is no longer available. Choose another target.
              </p>
            ) : null}
            {selectedProjectMissing ? (
              <p role="alert" className="ting-work-action-error">
                The selected tracker project is no longer available. Choose another project.
              </p>
            ) : null}
            {error ? (
              <p role="alert" className="ting-work-action-error">
                {error}
              </p>
            ) : null}
          </div>
        </div>
      )}
    </Modal>
  );
}
