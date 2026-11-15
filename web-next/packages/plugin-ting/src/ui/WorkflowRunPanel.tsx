/**
 * WorkflowRunPanel — the right-hand panel of the Workflows page while a
 * workflow is being viewed: what it is, a launch card, and the runs recorded
 * for it. It runs exactly the workflow version on the canvas.
 *
 * Owner: plugin-ting.
 */

import { useMemo, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { useOptionalService, useService } from '@niuulabs/plugin-sdk';
import { LoadingState, type RepoRecord } from '@niuulabs/ui';
import type { IDispatchBus, ITrackerBrowserService, TrackerIssue } from '../ports';
import type { Workflow } from '../domain/workflow';
import { useLaunchWorkflow } from './useWorkflows';
import {
  WorkflowLaunchForm,
  useWorkflowLaunchDraft,
  workflowLaunchRequest,
} from './WorkflowLaunchForm';
import { gateWaitsForPerson } from './WorkflowStrip';
import { WorkflowRunsCard } from './WorkflowRunsCard';
import { DispatchTargetSelect } from './DispatchView';
import { WorkflowIssuePicker, issueLaunchPrompt } from './WorkflowIssuePicker';

type RepoCatalogService = {
  getRepos(): Promise<RepoRecord[]>;
  getBranches(repoUrl: string): Promise<string[]>;
};

/** Gates a person has to answer before the run continues. */
export function humanGateCount(workflow: Workflow): number {
  return workflow.nodes.filter((node) => node.kind === 'gate' && gateWaitsForPerson(node)).length;
}

export function workflowSummary(workflow: Workflow): string {
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

export interface WorkflowRunPanelProps {
  /** The workflow on the canvas, at the version shown there. */
  workflow: Workflow;
  /** Repository and branch to prefill, from the page's search. */
  initialRepo?: string;
  initialBranch?: string;
}

export function WorkflowRunPanel({ workflow, initialRepo, initialBranch }: WorkflowRunPanelProps) {
  const navigate = useNavigate();
  const repoCatalog = useService<RepoCatalogService>('niuu.repos');
  const tracker = useOptionalService<ITrackerBrowserService>('ting.tracker');
  const dispatchBus = useOptionalService<IDispatchBus>('ting.dispatch');
  const launchWorkflow = useLaunchWorkflow();
  const [issuePickerOpen, setIssuePickerOpen] = useState(false);
  const [connectionId, setConnectionId] = useState('');
  const draft = useWorkflowLaunchDraft(workflow.id, {
    repo: initialRepo ?? '',
    branch: initialBranch ?? '',
  });

  const reposQuery = useQuery({
    queryKey: ['niuu', 'repos'],
    queryFn: () => repoCatalog.getRepos(),
  });
  // Which Forge runs the launch; left on Auto, Ting places it among the targets.
  const clustersQuery = useQuery({
    queryKey: ['ting', 'dispatch-clusters'],
    queryFn: () => dispatchBus!.getClusters(),
    enabled: dispatchBus !== undefined,
    retry: false,
  });
  const clusters = useMemo(
    () => (clustersQuery.data ?? []).filter((cluster) => cluster.enabled),
    [clustersQuery.data],
  );

  const unresolved = workflowHasUnresolvedRequirements(workflow);
  const canLaunch =
    !unresolved && draft.values.prompt.trim().length > 0 && !launchWorkflow.isPending;

  function handleIssuePicked(issue: TrackerIssue) {
    draft.update({ prompt: issueLaunchPrompt(issue) });
  }

  async function handleLaunch() {
    if (!canLaunch) return;
    draft.update({ error: '' });
    try {
      const result = await launchWorkflow.mutateAsync({
        workflowId: workflow.id,
        request: {
          ...workflowLaunchRequest(draft.values),
          ...(workflow.version ? { workflowVersion: workflow.version } : {}),
          ...(connectionId ? { connectionId } : {}),
        },
      });
      draft.reset();
      await navigate({
        to: '/ting/review/run/$slug',
        params: { slug: result.campaignSlug },
      });
    } catch (launchError) {
      draft.update({
        error: launchError instanceof Error ? launchError.message : 'Launch failed.',
      });
    }
  }

  return (
    <aside
      data-testid="workflow-run-panel"
      aria-label="Run workflow"
      className="ting-workflow-catalog__run"
    >
      <div className="niuu:flex niuu:flex-col niuu:gap-1.5">
        <span className="ting-workflow-catalog__scope">
          {workflow.scope === 'system' ? 'shared' : 'yours'}
        </span>
        <p className="ting-workflow-catalog__description">{workflowSummary(workflow)}</p>
      </div>

      <section className="ting-workflow-catalog__launch">
        <div role="heading" aria-level={2} className="ting-workflow-catalog__section-title">
          Run workflow
        </div>
        {tracker ? (
          <button
            type="button"
            data-testid="catalog-workflow-pick-issue"
            onClick={() => setIssuePickerOpen(true)}
            className="ting-workflow-catalog__pick-issue"
          >
            Use a tracker issue as the prompt
          </button>
        ) : null}
        {unresolved ? (
          <p
            data-testid="catalog-workflow-unresolved"
            className="niuu:mt-0 niuu:mb-3 niuu:rounded-md niuu:border niuu:border-warning/40 niuu:bg-warning/10 niuu:p-2.5 niuu:text-xs niuu:text-warning"
          >
            It has unresolved persona dependencies or local bindings. More lists them; choose Edit
            to resolve them before launch.
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
        {dispatchBus ? (
          <div className="niuu:mt-3">
            <DispatchTargetSelect
              label="Run on"
              clusters={clusters}
              selectedClusterId={connectionId}
              onChange={setConnectionId}
              isLoading={clustersQuery.isLoading}
            />
            {clustersQuery.error ? (
              <p role="alert">Could not load Forge targets: {clustersQuery.error.message}</p>
            ) : null}
          </div>
        ) : null}
        <div className="niuu:mt-4 niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
          <span className="niuu:text-xs niuu:text-text-muted">{gatesSentence(workflow)}</span>
          <button
            type="button"
            data-testid="catalog-workflow-launch"
            disabled={!canLaunch}
            onClick={() => void handleLaunch()}
            className="ting-workflow-catalog__launch-button"
          >
            {launchWorkflow.isPending ? 'Launching…' : 'Launch'}
          </button>
        </div>
      </section>

      <WorkflowRunsCard workflowId={workflow.id} />

      {tracker ? (
        <WorkflowIssuePicker
          open={issuePickerOpen}
          onOpenChange={setIssuePickerOpen}
          onPick={handleIssuePicked}
        />
      ) : null}
    </aside>
  );
}
