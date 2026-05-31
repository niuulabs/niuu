import { useMemo, useState, type FormEvent } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { BranchSelect, RepoSelect, type RepoRecord } from '@niuulabs/ui';
import { useCreateResearchCampaign } from './useResearch';
import { useWorkflows } from './useWorkflows';

type RepoCatalogService = {
  getRepos(): Promise<RepoRecord[]>;
};

function hasResearchTag(tags: string[] | undefined): boolean {
  return (tags ?? []).some((tag) => tag.trim().toLowerCase() === 'research');
}

export function ResearchNewPage() {
  const navigate = useNavigate();
  const repoCatalog = useService<RepoCatalogService>('niuu.repos');
  const createCampaign = useCreateResearchCampaign();
  const workflowsQuery = useWorkflows();
  const reposQuery = useQuery({
    queryKey: ['niuu', 'repos'],
    queryFn: () => repoCatalog.getRepos(),
  });
  const [question, setQuestion] = useState('');
  const [mode, setMode] = useState('exploratory');
  const [audience, setAudience] = useState('');
  const [deliverable, setDeliverable] = useState('');
  const [success, setSuccess] = useState('');
  const [constraints, setConstraints] = useState('');
  const [repo, setRepo] = useState('');
  const [branch, setBranch] = useState('dev');
  const [showAllWorkflows, setShowAllWorkflows] = useState(false);
  const [selectedWorkflowId, setSelectedWorkflowId] = useState('');

  const workflows = useMemo(() => workflowsQuery.data ?? [], [workflowsQuery.data]);
  const taggedWorkflows = useMemo(
    () => workflows.filter((workflow) => hasResearchTag(workflow.tags)),
    [workflows],
  );
  const visibleWorkflows = useMemo(() => {
    if (showAllWorkflows || taggedWorkflows.length === 0) return workflows;
    return taggedWorkflows;
  }, [showAllWorkflows, taggedWorkflows, workflows]);
  const effectiveSelectedWorkflowId =
    visibleWorkflows.find((workflow) => workflow.id === selectedWorkflowId)?.id ??
    visibleWorkflows[0]?.id ??
    '';
  const selectedWorkflow =
    visibleWorkflows.find((workflow) => workflow.id === effectiveSelectedWorkflowId) ?? null;
  const repos = reposQuery.data ?? [];

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const campaign = await createCampaign.mutateAsync({
      question,
      workflowId: effectiveSelectedWorkflowId || undefined,
      mode,
      audience,
      deliverable,
      success,
      constraints: constraints
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean),
      repo,
      branch,
    });
    void navigate({ to: '/ting/research/$slug', params: { slug: campaign.slug } });
  }

  return (
    <div className="niuu:h-full niuu:overflow-y-auto niuu:bg-bg-primary">
      <div className="niuu:mx-auto niuu:max-w-4xl niuu:px-6 niuu:py-8">
        <form
          onSubmit={handleSubmit}
          className="niuu:rounded-3xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-6 niuu:flex niuu:flex-col niuu:gap-5"
        >
          <div>
            <div className="niuu:text-[11px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.24em] niuu:text-text-faint">
              Research intake
            </div>
            <h1 className="niuu:m-0 niuu:mt-2 niuu:text-3xl niuu:font-semibold niuu:text-text-primary">
              Start a campaign
            </h1>
            <p className="niuu:mb-0 niuu:mt-2 niuu:text-sm niuu:text-text-secondary">
              Pick a research-compatible workflow, then let it create the notebook and durable
              memory trail in Mímir.
            </p>
          </div>

          <div className="niuu:rounded-2xl niuu:border niuu:border-border-subtle niuu:bg-bg-primary/50 niuu:p-4 niuu:flex niuu:flex-col niuu:gap-3">
            <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
              <div className="niuu:flex niuu:flex-col niuu:gap-1">
                <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">
                  Workflow
                </span>
                <span className="niuu:text-xs niuu:text-text-faint">
                  {taggedWorkflows.length > 0 && !showAllWorkflows
                    ? `Showing ${taggedWorkflows.length} workflow${taggedWorkflows.length === 1 ? '' : 's'} tagged research`
                    : taggedWorkflows.length > 0
                      ? `Showing all ${workflows.length} workflows`
                      : 'No research-tagged workflows found, so all workflows are shown'}
                </span>
              </div>
              {taggedWorkflows.length > 0 ? (
                <div className="niuu:inline-flex niuu:items-center niuu:gap-1 niuu:rounded-full niuu:border niuu:border-border niuu:bg-bg-elevated niuu:p-1">
                  <button
                    type="button"
                    onClick={() => setShowAllWorkflows(false)}
                    className={[
                      'niuu:rounded-full niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:font-medium',
                      !showAllWorkflows
                        ? 'niuu:bg-sky-400/15 niuu:text-sky-100'
                        : 'niuu:text-text-secondary',
                    ].join(' ')}
                  >
                    Research-tagged
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowAllWorkflows(true)}
                    className={[
                      'niuu:rounded-full niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:font-medium',
                      showAllWorkflows
                        ? 'niuu:bg-sky-400/15 niuu:text-sky-100'
                        : 'niuu:text-text-secondary',
                    ].join(' ')}
                  >
                    All workflows
                  </button>
                </div>
              ) : null}
            </div>

            <label className="niuu:flex niuu:flex-col niuu:gap-2">
              <select
                aria-label="Workflow"
                value={effectiveSelectedWorkflowId}
                onChange={(event) => setSelectedWorkflowId(event.target.value)}
                className="niuu:rounded-xl niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2.5 niuu:text-sm niuu:text-text-primary"
              >
                {visibleWorkflows.length > 0 ? (
                  visibleWorkflows.map((workflow) => (
                    <option key={workflow.id} value={workflow.id}>
                      {workflow.name}
                      {workflow.version ? ` · v${workflow.version}` : ''}
                    </option>
                  ))
                ) : (
                  <option value="">Default research workflow</option>
                )}
              </select>
            </label>

            {selectedWorkflow ? (
              <div className="niuu:flex niuu:flex-col niuu:gap-2 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-3">
                <div className="niuu:flex niuu:items-center niuu:gap-2 niuu:flex-wrap">
                  <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">
                    {selectedWorkflow.name}
                  </span>
                  {selectedWorkflow.version ? (
                    <span className="niuu:rounded-full niuu:border niuu:border-border niuu:px-2 niuu:py-0.5 niuu:font-mono niuu:text-[10px] niuu:text-text-faint">
                      v{selectedWorkflow.version}
                    </span>
                  ) : null}
                  {(selectedWorkflow.tags ?? []).map((tag) => (
                    <span
                      key={tag}
                      className="niuu:rounded-full niuu:border niuu:border-sky-300/25 niuu:bg-sky-400/10 niuu:px-2 niuu:py-0.5 niuu:font-mono niuu:text-[10px] niuu:uppercase niuu:tracking-[0.16em] niuu:text-sky-100"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
                {selectedWorkflow.description ? (
                  <p className="niuu:m-0 niuu:text-sm niuu:text-text-secondary">
                    {selectedWorkflow.description}
                  </p>
                ) : null}
              </div>
            ) : (
              <div className="niuu:flex niuu:flex-col niuu:gap-2 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-3">
                <div className="niuu:text-sm niuu:font-medium niuu:text-text-primary">
                  Default research workflow
                </div>
                <p className="niuu:m-0 niuu:text-sm niuu:text-text-secondary">
                  Ting will ask the backend to launch its default research-compatible workflow if
                  the workflow catalog is unavailable.
                </p>
              </div>
            )}
          </div>

          <label className="niuu:flex niuu:flex-col niuu:gap-2">
            <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">Question</span>
            <textarea
              required
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              rows={5}
              className="niuu:rounded-2xl niuu:border niuu:border-border niuu:bg-bg-primary niuu:p-4 niuu:text-sm niuu:text-text-primary"
              placeholder="What should this campaign answer?"
            />
          </label>

          <div className="niuu:grid niuu:gap-4 niuu:md:grid-cols-2">
            <label className="niuu:flex niuu:flex-col niuu:gap-2">
              <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">Mode</span>
              <select
                value={mode}
                onChange={(event) => setMode(event.target.value)}
                className="niuu:rounded-xl niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2.5 niuu:text-sm niuu:text-text-primary"
              >
                <option value="exploratory">Exploratory</option>
                <option value="evaluative">Evaluative</option>
                <option value="investigative">Investigative</option>
                <option value="monitoring">Monitoring</option>
              </select>
            </label>
            <label className="niuu:flex niuu:flex-col niuu:gap-2">
              <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">Audience</span>
              <input
                value={audience}
                onChange={(event) => setAudience(event.target.value)}
                className="niuu:rounded-xl niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2.5 niuu:text-sm niuu:text-text-primary"
                placeholder="Who is this for?"
              />
            </label>
            <label className="niuu:flex niuu:flex-col niuu:gap-2">
              <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">
                Deliverable
              </span>
              <input
                value={deliverable}
                onChange={(event) => setDeliverable(event.target.value)}
                className="niuu:rounded-xl niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2.5 niuu:text-sm niuu:text-text-primary"
                placeholder="memo, source pack, decision brief…"
              />
            </label>
            <label className="niuu:flex niuu:flex-col niuu:gap-2">
              <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">
                Success criteria
              </span>
              <input
                value={success}
                onChange={(event) => setSuccess(event.target.value)}
                className="niuu:rounded-xl niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2.5 niuu:text-sm niuu:text-text-primary"
                placeholder="How will we know this is done?"
              />
            </label>
          </div>

          <label className="niuu:flex niuu:flex-col niuu:gap-2">
            <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">
              Constraints
            </span>
            <textarea
              value={constraints}
              onChange={(event) => setConstraints(event.target.value)}
              rows={4}
              className="niuu:rounded-2xl niuu:border niuu:border-border niuu:bg-bg-primary niuu:p-4 niuu:text-sm niuu:text-text-primary"
              placeholder="One per line"
            />
          </label>

          <div className="niuu:grid niuu:gap-4 niuu:md:grid-cols-2">
            <label className="niuu:flex niuu:flex-col niuu:gap-2">
              <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">Repo</span>
              {repos.length > 0 ? (
                <RepoSelect
                  repos={repos}
                  value={repo}
                  onChange={(value) => {
                    const selectedRepo = repos.find((item) => item.cloneUrl === value);
                    setRepo(value);
                    setBranch(selectedRepo?.defaultBranch ?? '');
                  }}
                  placeholder="Select repository"
                  valueMode="cloneUrl"
                  testId="research-launch-repo-select"
                />
              ) : (
                <input
                  value={repo}
                  onChange={(event) => setRepo(event.target.value)}
                  className="niuu:rounded-xl niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2.5 niuu:text-sm niuu:text-text-primary"
                  placeholder="optional repository context"
                />
              )}
            </label>
            <label className="niuu:flex niuu:flex-col niuu:gap-2">
              <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">Branch</span>
              {repo && repos.length > 0 ? (
                <BranchSelect
                  repos={repos}
                  selectedRepos={repo}
                  value={branch}
                  onChange={setBranch}
                  placeholder="Select branch"
                  testId="research-launch-branch-select"
                />
              ) : (
                <input
                  value={branch}
                  onChange={(event) => setBranch(event.target.value)}
                  className="niuu:rounded-xl niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2.5 niuu:text-sm niuu:text-text-primary"
                />
              )}
            </label>
          </div>

          <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
            <button
              type="button"
              onClick={() => navigate({ to: '/ting/research' })}
              className="niuu:rounded-full niuu:border niuu:border-border niuu:bg-transparent niuu:px-4 niuu:py-2.5 niuu:text-sm niuu:text-text-secondary"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={
                createCampaign.isPending || question.trim().length === 0 || workflowsQuery.isLoading
              }
              className="niuu:rounded-full niuu:border niuu:border-sky-300/40 niuu:bg-sky-400/15 niuu:px-5 niuu:py-2.5 niuu:text-sm niuu:font-medium niuu:text-sky-100 niuu:disabled:opacity-50"
            >
              {createCampaign.isPending ? 'Launching…' : 'Launch campaign'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
