/**
 * SimpleWorkflowsPage — the Simple-mode face of /ting/workflows.
 *
 * One workflow at a time: what it is, how it runs (stages and the gates that
 * wait for you), a launch card, and the runs recorded for it. The builder is
 * one click away at /ting/workflows/build.
 *
 * Owner: plugin-ting.
 */

import { useState } from 'react';
import { Link, useNavigate, useSearch } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { useOptionalService, useService } from '@niuulabs/plugin-sdk';
import { LoadingState, cn, type RepoRecord } from '@niuulabs/ui';
import { Workflow as WorkflowIcon } from 'lucide-react';
import type { ITrackerBrowserService, TrackerIssue } from '../ports';
import type { Workflow } from '../domain/workflow';
import { useCreateWorkflow, useLaunchWorkflow, useWorkflows } from './useWorkflows';
import { useResearchCampaigns } from './useResearch';
import { useSpecCampaigns } from './useSpecs';
import {
  WorkflowLaunchForm,
  useWorkflowLaunchDraft,
  workflowLaunchRequest,
} from './WorkflowLaunchForm';
import { WorkflowStrip, gateWaitsForPerson } from './WorkflowStrip';
import { WorkflowRunsCard } from './WorkflowRunsCard';
import { WorkflowIssuePicker, issueLaunchPrompt } from './WorkflowIssuePicker';
import { WorkflowImportDialog } from './WorkflowImportDialog';
import { usePersonasBrowser } from './settings/usePersonasBrowser';
import { useWorkflowRegistryMounts } from './useWorkflowRegistryMounts';
import './SimpleWorkflowsPage.css';

interface SimpleWorkflowsSearch {
  workflow?: string;
  repo?: string;
  branch?: string;
}

type RepoCatalogService = {
  getRepos(): Promise<RepoRecord[]>;
  getBranches(repoUrl: string): Promise<string[]>;
};

/** Gates a person has to answer before the run continues. */
function humanGateCount(workflow: Workflow): number {
  return workflow.nodes.filter((node) => node.kind === 'gate' && gateWaitsForPerson(node)).length;
}

function stageCount(workflow: Workflow): number {
  return workflow.nodes.filter((node) => node.kind === 'stage').length;
}

function workflowSummary(workflow: Workflow): string {
  if (workflow.description?.trim()) return workflow.description.trim();
  const stages = workflow.nodes
    .filter((node) => node.kind === 'stage')
    .map((node) => node.label)
    .filter(Boolean);
  if (stages.length === 0) return 'No stages yet.';
  return stages.join(' · ');
}

function gatesSentence(workflow: Workflow): string {
  const gates = humanGateCount(workflow);
  if (gates === 0) return 'It runs start to finish without stopping for you.';
  if (gates === 1) return 'Stops for you at 1 gate';
  return `Stops for you at ${gates} gates`;
}

function workflowHasUnresolvedRequirements(workflow: Workflow): boolean {
  if ((workflow.requirements ?? []).some((requirement) => !requirement.resolved)) return true;
  return Object.values(workflow.personaDependencies ?? {}).some(
    (dependency) => dependency.resolved === false,
  );
}

function WorkflowOptionCard({
  workflow,
  selected,
  runCount,
  onSelect,
}: {
  workflow: Workflow;
  selected: boolean;
  runCount: number;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      data-testid={`simple-workflow-${workflow.id}`}
      data-selected={selected ? 'true' : undefined}
      onClick={onSelect}
      className={cn(
        'niuu:flex niuu:w-full niuu:items-start niuu:gap-3 niuu:rounded-lg niuu:border niuu:px-3 niuu:py-2.5 niuu:text-left niuu:cursor-pointer niuu:transition-colors',
        selected
          ? 'niuu:border-brand/50 niuu:bg-brand/10'
          : 'niuu:border-transparent niuu:bg-transparent niuu:hover:border-border-subtle niuu:hover:bg-bg-tertiary',
      )}
    >
      <span className="niuu:mt-0.5 niuu:flex niuu:size-7 niuu:shrink-0 niuu:items-center niuu:justify-center niuu:rounded-full niuu:border niuu:border-border-subtle niuu:bg-bg-elevated niuu:text-text-secondary">
        <WorkflowIcon size={14} aria-hidden="true" />
      </span>
      <span className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col niuu:gap-0.5">
        <span className="ting-workflow-catalog__row-title niuu:truncate niuu:font-semibold niuu:text-text-primary">
          {workflow.name}
        </span>
        <span className="niuu:flex niuu:min-w-0 niuu:flex-col niuu:gap-1">
          <span className="niuu:min-w-0 niuu:flex-1 niuu:truncate niuu:text-xs niuu:text-text-muted">
            {workflowSummary(workflow)}
          </span>
          <span className="niuu:shrink-0 niuu:font-mono niuu:text-xs niuu:text-text-faint">
            {humanGateCount(workflow)} gates · {stageCount(workflow)} stages
            {runCount > 0 ? ` · ran ${runCount}×` : ''}
          </span>
        </span>
      </span>
    </button>
  );
}

/** How many campaign runs each workflow has, keyed by workflow id. */
function useWorkflowRunCounts(): Record<string, number> {
  const specs = useSpecCampaigns();
  const research = useResearchCampaigns();
  const counts: Record<string, number> = {};
  for (const campaign of [...(specs.data ?? []), ...(research.data ?? [])]) {
    counts[campaign.workflowId] = (counts[campaign.workflowId] ?? 0) + 1;
  }
  return counts;
}

export function SimpleWorkflowsPage() {
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as SimpleWorkflowsSearch;
  const repoCatalog = useService<RepoCatalogService>('niuu.repos');
  const tracker = useOptionalService<ITrackerBrowserService>('ting.tracker');
  const { data: workflows, isLoading, isError, error } = useWorkflows();
  const runCounts = useWorkflowRunCounts();
  const createWorkflow = useCreateWorkflow();
  const launchWorkflow = useLaunchWorkflow();
  const [issuePickerOpen, setIssuePickerOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const { data: personas = [] } = usePersonasBrowser();
  const { data: registryMounts = [] } = useWorkflowRegistryMounts();

  const reposQuery = useQuery({
    queryKey: ['niuu', 'repos'],
    queryFn: () => repoCatalog.getRepos(),
  });

  const selected =
    workflows?.find((workflow) => workflow.id === search.workflow) ?? workflows?.[0] ?? null;
  const draft = useWorkflowLaunchDraft(selected?.id ?? 'none', {
    repo: search.repo ?? '',
    branch: search.branch ?? '',
  });

  function selectWorkflow(id: string) {
    void navigate({ to: '/ting/workflows', search: { ...search, workflow: id } });
  }

  function openBuilder(id: string) {
    void navigate({ to: '/ting/workflows/build', search: { id } });
  }

  function handleNew() {
    createWorkflow.mutate(undefined, {
      onSuccess: (created) => openBuilder(created.id),
    });
  }

  function handleIssuePicked(issue: TrackerIssue) {
    draft.update({ prompt: issueLaunchPrompt(issue) });
  }

  async function handleLaunch() {
    if (!selected || draft.values.prompt.trim().length === 0) return;
    draft.update({ error: '' });
    try {
      const result = await launchWorkflow.mutateAsync({
        workflowId: selected.id,
        request: workflowLaunchRequest(draft.values),
      });
      draft.reset();
      await navigate({
        to: '/volundr/sessions/$sessionId',
        params: { sessionId: result.sessionId },
      });
    } catch (launchError) {
      draft.update({
        error: launchError instanceof Error ? launchError.message : 'Launch failed.',
      });
    }
  }

  const canLaunch =
    selected !== null &&
    !workflowHasUnresolvedRequirements(selected) &&
    draft.values.prompt.trim().length > 0 &&
    !launchWorkflow.isPending;

  return (
    <div data-testid="simple-workflows-page" className="ting-workflow-catalog">
      <aside className="ting-workflow-catalog__sidebar" aria-label="Workflow catalog">
        <div className="ting-workflow-catalog__heading">
          <h2>Workflows</h2>
          <div className="niuu:flex niuu:items-center niuu:gap-2">
            <button
              type="button"
              data-testid="simple-workflow-import"
              onClick={() => setImportOpen(true)}
              className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-elevated niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:text-text-secondary niuu:cursor-pointer niuu:hover:text-text-primary"
            >
              Import
            </button>
            <button
              type="button"
              data-testid="simple-workflow-new"
              onClick={handleNew}
              disabled={createWorkflow.isPending}
              className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-elevated niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:text-text-secondary niuu:cursor-pointer niuu:hover:text-text-primary niuu:disabled:opacity-50"
            >
              New
            </button>
          </div>
        </div>

        <div className="niuu:flex niuu:flex-1 niuu:flex-col niuu:gap-1 niuu:overflow-y-auto niuu:px-3 niuu:pb-3">
          {isLoading ? <LoadingState label="Loading workflows…" /> : null}
          {isError ? (
            <p className="niuu:m-0 niuu:px-2 niuu:text-xs niuu:text-critical" role="alert">
              {error instanceof Error ? error.message : 'Failed to load workflows.'}
            </p>
          ) : null}
          {!isLoading && !isError && (workflows?.length ?? 0) === 0 ? (
            <p className="niuu:m-0 niuu:px-2 niuu:text-xs niuu:text-text-muted">
              No workflows yet. New builds your first one.
            </p>
          ) : null}
          {workflows?.map((workflow) => (
            <WorkflowOptionCard
              key={workflow.id}
              workflow={workflow}
              selected={workflow.id === selected?.id}
              runCount={runCounts[workflow.id] ?? 0}
              onSelect={() => selectWorkflow(workflow.id)}
            />
          ))}
        </div>
      </aside>

      {importOpen ? (
        <WorkflowImportDialog
          open
          workflows={workflows ?? []}
          personas={personas}
          registryMounts={registryMounts}
          onClose={() => setImportOpen(false)}
          onImported={(workflow) => selectWorkflow(workflow.id)}
        />
      ) : null}

      <main className="ting-workflow-catalog__detail">
        {!isLoading && !selected ? (
          <p className="niuu:m-0 niuu:text-sm niuu:text-text-muted">No workflow selected yet.</p>
        ) : null}

        {selected ? (
          <div className="niuu:flex niuu:flex-col niuu:gap-5">
            <header className="ting-workflow-catalog__header">
              <div className="niuu:min-w-0 niuu:flex-1">
                <div className="niuu:flex niuu:items-center niuu:gap-2.5">
                  <h1>{selected.name}</h1>
                  <span className="ting-workflow-catalog__scope">
                    {selected.scope === 'system' ? 'shared' : 'yours'}
                  </span>
                </div>
              </div>
              <Link
                to="/ting/workflows/build"
                search={{ id: selected.id }}
                data-testid="simple-workflow-edit"
                className="ting-workflow-catalog__builder"
              >
                <WorkflowIcon size={15} aria-hidden="true" /> Open in builder
              </Link>
            </header>

            <p className="ting-workflow-catalog__description">{workflowSummary(selected)}</p>

            <section className="ting-workflow-catalog__preview">
              <div role="heading" aria-level={2} className="ting-workflow-catalog__section-title">
                How it runs
              </div>
              <WorkflowStrip nodes={selected.nodes} edges={selected.edges} />
            </section>

            <div className="ting-workflow-catalog__panels">
              <section className="ting-workflow-catalog__launch">
                <div role="heading" aria-level={2} className="ting-workflow-catalog__section-title">
                  Run workflow
                </div>
                {tracker ? (
                  <button
                    type="button"
                    data-testid="simple-workflow-pick-issue"
                    onClick={() => setIssuePickerOpen(true)}
                    className="ting-workflow-catalog__pick-issue"
                  >
                    Use a tracker issue as the prompt
                  </button>
                ) : null}
                {workflowHasUnresolvedRequirements(selected) ? (
                  <p
                    data-testid="simple-workflow-unresolved"
                    className="niuu:mt-0 niuu:mb-3 niuu:rounded-md niuu:border niuu:border-warning/40 niuu:bg-warning/10 niuu:p-2.5 niuu:text-xs niuu:text-warning"
                  >
                    Resolve imported persona dependencies and local bindings in the Builder before
                    launch.
                  </p>
                ) : null}
                {reposQuery.isFetching ? <LoadingState label="Loading repositories…" /> : null}
                {reposQuery.error ? (
                  <p role="alert">Could not load repositories: {reposQuery.error.message}</p>
                ) : null}
                <WorkflowLaunchForm
                  loadBranches={repoCatalog.getBranches}
                  values={draft.values}
                  onChange={draft.update}
                  repos={reposQuery.data ?? []}
                  showSessionName={false}
                  promptLabel="What should it do?"
                  promptRows={3}
                  onPromptSubmit={() => void handleLaunch()}
                />
                <div className="niuu:mt-4 niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
                  <span className="niuu:text-xs niuu:text-text-muted">
                    {gatesSentence(selected)}
                  </span>
                  <button
                    type="button"
                    data-testid="simple-workflow-launch"
                    disabled={!canLaunch}
                    onClick={() => void handleLaunch()}
                    className="ting-workflow-catalog__launch-button"
                  >
                    {launchWorkflow.isPending ? 'Launching…' : 'Launch'}
                  </button>
                </div>
              </section>

              <WorkflowRunsCard workflowId={selected.id} />
            </div>
          </div>
        ) : null}
      </main>

      {tracker ? (
        <WorkflowIssuePicker
          open={issuePickerOpen}
          onOpenChange={setIssuePickerOpen}
          onPick={handleIssuePicked}
        />
      ) : null}
    </div>
  );
}
