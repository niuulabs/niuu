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
import { useWorkflows, useWorkflowVersions } from './useWorkflows';

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
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onImported: (saga: Saga) => void;
  connectedProjects: WorkSummary[];
  onOpenProject: (project: WorkSummary) => void;
}) {
  const tracker = useService<ITrackerBrowserService>('ting.tracker');
  const repos = useService<RepoCatalogService>('niuu.repos');
  const dispatch = useService<IDispatchBus>('ting.dispatch');
  const workflowsQuery = useWorkflows();
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
  const [workflowId, setWorkflowId] = useState('');
  const [workflowVersion, setWorkflowVersion] = useState('');
  const [workflowVersionExplicit, setWorkflowVersionExplicit] = useState(false);
  const [targetId, setTargetId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const projects = projectsQuery.data ?? [];
  const catalog = reposQuery.data ?? [];
  const workflows = workflowsQuery.data ?? [];
  if (open && !projectSourceKey && projects[0]) {
    setProjectSourceKey(projectKey(projects[0]));
  }
  const selectedProject = projectSourceKey
    ? (projects.find((project) => projectKey(project) === projectSourceKey) ?? null)
    : (projects[0] ?? null);
  const selectedProjectMissing = Boolean(projectSourceKey && !selectedProject);
  const selectedWorkflow = workflows.find((workflow) => workflow.id === workflowId) ?? null;
  const selectedWorkflowMissing = Boolean(workflowId && !selectedWorkflow);
  const versionsQuery = useWorkflowVersions(workflowId);
  const versions = versionsQuery.data ?? [];
  const selectedVersion = workflowVersion || selectedWorkflow?.version || '';
  const selectedVersionMissing = Boolean(
    workflowId &&
    workflowVersion &&
    !versionsQuery.isFetching &&
    !versionsQuery.isError &&
    (versions.length > 0 || workflowVersionExplicit) &&
    !versions.some((version) => version.version === workflowVersion),
  );
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
  const selectedVersionUnavailable = Boolean(workflowId && versionsQuery.isError);
  const canImport = Boolean(
    selectedProject &&
    !selectedProjectMissing &&
    !existingProject &&
    repo.trim() &&
    branch.trim() &&
    !selectedTargetMissing &&
    !selectedWorkflowMissing &&
    !selectedVersionUnavailable &&
    !selectedVersionMissing &&
    !versionsQuery.isFetching &&
    !busy,
  );

  function reset() {
    setProjectSourceKey('');
    setRepo('');
    setBranch('');
    setWorkflowId('');
    setWorkflowVersion('');
    setWorkflowVersionExplicit(false);
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
        [repo],
        branch,
        target?.instanceId ?? undefined,
        {
          repoRefs: [{ repo, branch }],
          trackerConnectionId: selectedProject.trackerConnectionId,
          target: target
            ? { mode: 'instance', instanceId: target.instanceId ?? target.connectionId }
            : { mode: 'default' },
          ...(workflowId ? { workflowId, workflowVersion: selectedVersion || undefined } : {}),
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
      description="Connect its tickets to a repository and workflow. Importing does not start execution."
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
      {projectsQuery.isLoading ||
      reposQuery.isLoading ||
      workflowsQuery.isLoading ||
      targetsQuery.isLoading ? (
        <LoadingState label="Loading connected projects…" />
      ) : projectsQuery.isError ||
        reposQuery.isError ||
        workflowsQuery.isError ||
        targetsQuery.isError ? (
        <ErrorState
          title="Project setup could not be loaded"
          message={
            [projectsQuery.error, reposQuery.error, workflowsQuery.error, targetsQuery.error].find(
              Boolean,
            ) instanceof Error
              ? (
                  [
                    projectsQuery.error,
                    reposQuery.error,
                    workflowsQuery.error,
                    targetsQuery.error,
                  ].find(Boolean) as Error
                ).message
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
            <label>
              <span>Repository</span>
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
            <div className="ting-tracker-import__pair">
              <label>
                <span>Workflow</span>
                <select
                  value={workflowId}
                  onChange={(event) => {
                    const nextWorkflowId = event.target.value;
                    setWorkflowId(nextWorkflowId);
                    setWorkflowVersion(
                      workflows.find((workflow) => workflow.id === nextWorkflowId)?.version ?? '',
                    );
                    setWorkflowVersionExplicit(false);
                  }}
                >
                  <option value="">Use project default</option>
                  {workflows.map((workflow) => (
                    <option key={workflow.id} value={workflow.id}>
                      {workflow.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>Version</span>
                <select
                  value={selectedVersion}
                  onChange={(event) => {
                    setWorkflowVersion(event.target.value);
                    setWorkflowVersionExplicit(true);
                  }}
                  disabled={!workflowId || versionsQuery.isLoading}
                >
                  {!workflowId ? <option value="">Default</option> : null}
                  {selectedVersionMissing ? (
                    <option value={workflowVersion}>{workflowVersion} · unavailable</option>
                  ) : null}
                  {workflowId && versions.length === 0 && selectedWorkflow ? (
                    <option value={selectedWorkflow.version}>{selectedWorkflow.version}</option>
                  ) : null}
                  {versions.map((version) => (
                    <option key={version.documentRevision} value={version.version}>
                      {version.version}
                      {version.isHead ? ' · current' : ''}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <label>
              <span>Execution target</span>
              <select value={targetId} onChange={(event) => setTargetId(event.target.value)}>
                <option value="">Let Ting choose</option>
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
            <p className="ting-tracker-import__effect">
              {existingProject
                ? 'This source-qualified project is already connected. Open it to review its current setup and tickets.'
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
                Open connected project
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
            {selectedWorkflowMissing ? (
              <p role="alert" className="ting-work-action-error">
                The selected workflow is no longer available. Choose another workflow.
              </p>
            ) : null}
            {selectedVersionMissing ? (
              <p role="alert" className="ting-work-action-error">
                The selected workflow version is no longer available. Choose another version.
              </p>
            ) : null}
            {selectedVersionUnavailable ? (
              <p role="alert" className="ting-work-action-error">
                Workflow versions could not be loaded. Retry before importing this project.
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
