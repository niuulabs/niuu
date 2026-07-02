import { useMemo, useState, type FormEvent } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { BranchSelect, RepoSelect, type RepoRecord } from '@niuulabs/ui';
import type { IDispatchBus } from '../ports';
import { useCreateSpecCampaign } from './useSpecs';
import { useWorkflows } from './useWorkflows';

type RepoCatalogService = {
  getRepos(): Promise<RepoRecord[]>;
};

function isSpecWorkflow(name: string, tags?: string[]): boolean {
  const normalizedName = name.trim().toLowerCase();
  return (
    normalizedName === 'specification stack' ||
    (tags ?? []).some((tag) => ['spec', 'specification'].includes(tag.trim().toLowerCase()))
  );
}

export function SpecsNewPage() {
  const navigate = useNavigate();
  const repoCatalog = useService<RepoCatalogService>('niuu.repos');
  const dispatchBus = useService<IDispatchBus>('ting.dispatch');
  const createCampaign = useCreateSpecCampaign();
  const workflowsQuery = useWorkflows();
  const reposQuery = useQuery({
    queryKey: ['niuu', 'repos'],
    queryFn: () => repoCatalog.getRepos(),
  });
  const targetsQuery = useQuery({
    queryKey: ['ting', 'dispatch', 'targets'],
    queryFn: () => dispatchBus.getClusters(),
  });

  const [title, setTitle] = useState('');
  const [prompt, setPrompt] = useState('');
  const [context, setContext] = useState('');
  const [selectedRepo, setSelectedRepo] = useState('');
  const [selectedRepos, setSelectedRepos] = useState<string[]>([]);
  const [branch, setBranch] = useState('dev');
  const [selectedWorkflowId, setSelectedWorkflowId] = useState('');
  const [showAllWorkflows, setShowAllWorkflows] = useState(false);
  const [selectedConnectionId, setSelectedConnectionId] = useState('');

  const workflows = workflowsQuery.data ?? [];
  const specWorkflows = useMemo(
    () => workflows.filter((workflow) => isSpecWorkflow(workflow.name, workflow.tags)),
    [workflows],
  );
  const visibleWorkflows =
    showAllWorkflows || specWorkflows.length === 0 ? workflows : specWorkflows;
  const effectiveWorkflowId =
    visibleWorkflows.find((workflow) => workflow.id === selectedWorkflowId)?.id ??
    visibleWorkflows[0]?.id ??
    '';
  const selectedWorkflow =
    visibleWorkflows.find((workflow) => workflow.id === effectiveWorkflowId) ?? null;
  const repos = reposQuery.data ?? [];
  const targets = useMemo(
    () => (targetsQuery.data ?? []).filter((target) => target.enabled),
    [targetsQuery.data],
  );
  const effectiveConnectionId =
    targets.find((target) => target.connectionId === selectedConnectionId)?.connectionId ??
    targets[0]?.connectionId ??
    '';
  const selectedTarget =
    targets.find((target) => target.connectionId === effectiveConnectionId) ?? null;

  function addSelectedRepo(value: string) {
    if (!value || selectedRepos.includes(value)) return;
    const next = [...selectedRepos, value];
    setSelectedRepos(next);
    const repo = repos.find((item) => item.cloneUrl === value);
    if (next.length === 1) setBranch(repo?.defaultBranch ?? branch);
    setSelectedRepo('');
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const campaign = await createCampaign.mutateAsync({
      prompt,
      name: title.trim() || undefined,
      workflowId: effectiveWorkflowId || undefined,
      repos: selectedRepos,
      repo: selectedRepos[0] ?? '',
      branch,
      context,
      connectionId: effectiveConnectionId || undefined,
    });
    void navigate({ to: '/ting/specs/$slug', params: { slug: campaign.slug } });
  }

  return (
    <div className="niuu:h-full niuu:overflow-y-auto niuu:bg-bg-primary">
      <div className="niuu:mx-auto niuu:max-w-4xl niuu:px-6 niuu:py-8">
        <form
          onSubmit={handleSubmit}
          className="niuu:flex niuu:flex-col niuu:gap-5 niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-6"
        >
          <div>
            <div className="niuu:text-[11px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.24em] niuu:text-text-faint">
              Specs
            </div>
            <h1 className="niuu:m-0 niuu:mt-2 niuu:text-3xl niuu:font-semibold niuu:text-text-primary">
              Start a spec
            </h1>
          </div>

          <div className="niuu:flex niuu:flex-col niuu:gap-3 niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-primary/50 niuu:p-4">
            <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
              <div className="niuu:flex niuu:flex-col niuu:gap-1">
                <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">
                  Workflow
                </span>
                <span className="niuu:text-xs niuu:text-text-faint">
                  {selectedWorkflow?.name ?? 'Specification Stack'}
                </span>
              </div>
              {specWorkflows.length > 0 ? (
                <button
                  type="button"
                  onClick={() => setShowAllWorkflows((value) => !value)}
                  className="niuu:rounded-full niuu:border niuu:border-border niuu:bg-bg-elevated niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:text-text-secondary"
                >
                  {showAllWorkflows ? 'Spec workflows' : 'All workflows'}
                </button>
              ) : null}
            </div>
            <select
              aria-label="Workflow"
              value={effectiveWorkflowId}
              onChange={(event) => setSelectedWorkflowId(event.target.value)}
              className="niuu:rounded-lg niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2.5 niuu:text-sm niuu:text-text-primary"
            >
              {visibleWorkflows.length > 0 ? (
                visibleWorkflows.map((workflow) => (
                  <option key={workflow.id} value={workflow.id}>
                    {workflow.name}
                    {workflow.version ? ` · v${workflow.version}` : ''}
                  </option>
                ))
              ) : (
                <option value="">Backend default workflow</option>
              )}
            </select>
          </div>

          <div className="niuu:flex niuu:flex-col niuu:gap-3 niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-primary/50 niuu:p-4">
            <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">
              Execution target
            </span>
            <select
              aria-label="Execution target"
              value={effectiveConnectionId}
              onChange={(event) => setSelectedConnectionId(event.target.value)}
              disabled={targetsQuery.isLoading || targets.length === 0}
              className="niuu:rounded-lg niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2.5 niuu:text-sm niuu:text-text-primary niuu:disabled:opacity-60"
            >
              {targets.length > 0 ? (
                targets.map((target) => (
                  <option key={target.connectionId} value={target.connectionId}>
                    {target.name}
                    {target.tags?.length ? ` · ${target.tags.join(', ')}` : ''}
                  </option>
                ))
              ) : (
                <option value="">Backend default target</option>
              )}
            </select>
            {selectedTarget ? (
              <span className="niuu:text-xs niuu:text-text-faint">
                {selectedTarget.tags?.join(', ') || selectedTarget.name}
              </span>
            ) : null}
          </div>

          <label className="niuu:flex niuu:flex-col niuu:gap-2">
            <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">Title</span>
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              className="niuu:rounded-lg niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2.5 niuu:text-sm niuu:text-text-primary"
              placeholder="Short name"
            />
          </label>

          <label className="niuu:flex niuu:flex-col niuu:gap-2">
            <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">Brief</span>
            <textarea
              required
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              rows={7}
              className="niuu:rounded-lg niuu:border niuu:border-border niuu:bg-bg-primary niuu:p-4 niuu:text-sm niuu:text-text-primary"
              placeholder="What should the PRD/SRD/SDD stack specify?"
            />
          </label>

          <label className="niuu:flex niuu:flex-col niuu:gap-2">
            <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">Context</span>
            <textarea
              value={context}
              onChange={(event) => setContext(event.target.value)}
              rows={4}
              className="niuu:rounded-lg niuu:border niuu:border-border niuu:bg-bg-primary niuu:p-4 niuu:text-sm niuu:text-text-primary"
              placeholder="Constraints, audiences, boundaries, source notes"
            />
          </label>

          <div className="niuu:grid niuu:gap-4 niuu:md:grid-cols-2">
            <label className="niuu:flex niuu:flex-col niuu:gap-2">
              <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">
                Repositories
              </span>
              {repos.length > 0 ? (
                <RepoSelect
                  repos={repos}
                  value={selectedRepo}
                  onChange={addSelectedRepo}
                  excludedRepos={selectedRepos}
                  placeholder="Add repository"
                  valueMode="cloneUrl"
                  testId="spec-launch-repo-select"
                />
              ) : (
                <input
                  value={selectedRepo}
                  onChange={(event) => setSelectedRepo(event.target.value)}
                  onBlur={() => addSelectedRepo(selectedRepo)}
                  className="niuu:rounded-lg niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2.5 niuu:text-sm niuu:text-text-primary"
                  placeholder="optional repository context"
                />
              )}
              {selectedRepos.length > 0 ? (
                <div className="niuu:flex niuu:flex-wrap niuu:gap-2">
                  {selectedRepos.map((repo) => (
                    <button
                      key={repo}
                      type="button"
                      onClick={() =>
                        setSelectedRepos((items) => items.filter((item) => item !== repo))
                      }
                      className="niuu:rounded-full niuu:border niuu:border-border niuu:bg-bg-elevated niuu:px-3 niuu:py-1 niuu:text-xs niuu:text-text-secondary"
                    >
                      {repo} ×
                    </button>
                  ))}
                </div>
              ) : null}
            </label>
            <label className="niuu:flex niuu:flex-col niuu:gap-2">
              <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">Branch</span>
              {selectedRepos.length > 0 && repos.length > 0 ? (
                <BranchSelect
                  repos={repos}
                  selectedRepos={selectedRepos[0] ?? ''}
                  value={branch}
                  onChange={setBranch}
                  placeholder="Select branch"
                  testId="spec-launch-branch-select"
                />
              ) : (
                <input
                  value={branch}
                  onChange={(event) => setBranch(event.target.value)}
                  className="niuu:rounded-lg niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2.5 niuu:text-sm niuu:text-text-primary"
                />
              )}
            </label>
          </div>

          <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
            <button
              type="button"
              onClick={() => navigate({ to: '/ting/specs' })}
              className="niuu:rounded-full niuu:border niuu:border-border niuu:bg-transparent niuu:px-4 niuu:py-2.5 niuu:text-sm niuu:text-text-secondary"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={createCampaign.isPending || prompt.trim().length === 0}
              className="niuu:rounded-full niuu:border niuu:border-sky-300/40 niuu:bg-sky-400/15 niuu:px-5 niuu:py-2.5 niuu:text-sm niuu:font-medium niuu:text-sky-100 niuu:disabled:opacity-50"
            >
              {createCampaign.isPending ? 'Launching…' : 'Launch spec'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
