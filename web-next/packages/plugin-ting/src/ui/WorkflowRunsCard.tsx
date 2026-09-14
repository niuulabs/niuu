/**
 * WorkflowRunsCard — the campaign runs recorded for one workflow.
 *
 * Spec and research campaigns both carry the workflow they ran, so both are
 * listed. Only spec campaigns can be decided from here: the review endpoint
 * (`POST /specs/campaigns/{slug}/review`) exists for that surface alone, and a
 * research campaign is opened instead of pretending it can be approved.
 *
 * Owner: plugin-ting.
 */

import { useState } from 'react';
import { Link } from '@tanstack/react-router';
import { useQueryClient } from '@tanstack/react-query';
import { LoadingState, StateDot, cn, relTime, type DotState } from '@niuulabs/ui';
import type { ResearchCampaign, SpecCampaign } from '../ports';
import {
  actionableSpecGates,
  activeStageLabel,
  statusForCampaign,
  type CampaignReviewStatus,
} from '../domain/campaignProgress';
import { useResearchCampaigns } from './useResearch';
import { useReviewSpecCampaign, useSpecCampaigns } from './useSpecs';
import { useCampaignEvents } from './useCampaignEvents';

const CAMPAIGN_QUERY_KEYS = [
  ['ting', 'specs', 'campaigns'],
  ['ting', 'research', 'campaigns'],
];

const SPEC_CAMPAIGNS_KEY = ['ting', 'specs', 'campaigns'];

type RunSurface = 'specs' | 'research';

interface WorkflowRun {
  campaign: ResearchCampaign;
  surface: RunSurface;
}

const STATUS_DOT: Record<CampaignReviewStatus, DotState> = {
  review: 'attention',
  running: 'running',
  published: 'merged',
  draft: 'queued',
};

const STAGE_DOT_CLASS: Record<string, string> = {
  complete: 'niuu:bg-status-emerald',
  active: 'niuu:bg-brand',
  blocked: 'niuu:bg-status-amber',
};

function statusLine(campaign: ResearchCampaign): string {
  const status = statusForCampaign(campaign);
  if (status === 'review') return `waiting on you · ${activeStageLabel(campaign)}`;
  if (status === 'published') return `finished · ${relTime(campaign.updatedAt)}`;
  if (status === 'draft') return 'queued';
  return `${activeStageLabel(campaign)} · ${relTime(campaign.updatedAt)}`;
}

function StageDots({ campaign }: { campaign: ResearchCampaign }) {
  if (campaign.stageState.length === 0) return null;
  return (
    <span className="niuu:flex niuu:items-center niuu:gap-1" aria-hidden="true">
      {campaign.stageState.map((stage) => (
        <span
          key={stage.stageId}
          data-status={stage.status}
          className={cn(
            'niuu:size-1.5 niuu:rounded-full',
            STAGE_DOT_CLASS[stage.status] ?? 'niuu:bg-border',
          )}
        />
      ))}
    </span>
  );
}

function RunRow({ run }: { run: WorkflowRun }) {
  const { campaign, surface } = run;
  const queryClient = useQueryClient();
  const review = useReviewSpecCampaign(campaign.slug);
  const [notes, setNotes] = useState<string | null>(null);
  const gate = surface === 'specs' ? (actionableSpecGates(campaign)[0] ?? null) : null;
  const status = statusForCampaign(campaign);
  const detailPath = surface === 'specs' ? '/ting/specs/$slug' : '/ting/research/$slug';

  function decide(decision: 'approve' | 'changes_requested', reviewNotes: string) {
    if (!gate) return;
    const before = queryClient.getQueryData<SpecCampaign[]>(SPEC_CAMPAIGNS_KEY);
    queryClient.setQueryData<SpecCampaign[]>(SPEC_CAMPAIGNS_KEY, (previous) =>
      previous?.map((entry) =>
        entry.id === campaign.id
          ? {
              ...entry,
              status: decision === 'approve' ? 'running' : entry.status,
              metadata: { ...entry.metadata, pending_workflow_gates: [] },
            }
          : entry,
      ),
    );
    review.mutate(
      {
        decision,
        notes: reviewNotes,
        gateId: gate.id,
        nodeId: gate.nodeId,
      },
      {
        // The gate is still open if the review did not land: put it back.
        onError: () => queryClient.setQueryData(SPEC_CAMPAIGNS_KEY, before),
      },
    );
    setNotes(null);
  }

  return (
    <li
      data-testid={`workflow-run-${campaign.slug}`}
      className="niuu:flex niuu:flex-col niuu:gap-2 niuu:border-b niuu:border-border-subtle niuu:py-2.5 niuu:last:border-b-0"
    >
      <div className="niuu:flex niuu:items-center niuu:gap-2.5">
        <StateDot state={STATUS_DOT[status]} pulse={status === 'running'} />
        <Link
          to={detailPath}
          params={{ slug: campaign.slug }}
          className="niuu:min-w-0 niuu:flex-1 niuu:no-underline"
        >
          <span className="niuu:block niuu:truncate niuu:text-[13px] niuu:font-medium niuu:text-text-primary">
            {campaign.name}
          </span>
          <span className="niuu:block niuu:truncate niuu:font-mono niuu:text-[10px] niuu:text-text-faint">
            {statusLine(campaign)}
          </span>
        </Link>
        <StageDots campaign={campaign} />
      </div>

      {gate ? (
        <div className="niuu:flex niuu:flex-col niuu:gap-2 niuu:pl-[18px]">
          <span className="niuu:text-[11px] niuu:text-text-secondary">{gate.summary}</span>
          {notes === null ? (
            <div className="niuu:flex niuu:items-center niuu:gap-2">
              <button
                type="button"
                data-testid={`workflow-run-approve-${campaign.slug}`}
                disabled={review.isPending}
                onClick={() => decide('approve', '')}
                className="niuu:rounded-md niuu:border niuu:border-brand/50 niuu:bg-brand/10 niuu:px-2.5 niuu:py-1 niuu:text-[11px] niuu:text-brand niuu:cursor-pointer niuu:disabled:opacity-50"
              >
                Approve
              </button>
              <button
                type="button"
                data-testid={`workflow-run-changes-${campaign.slug}`}
                disabled={review.isPending}
                onClick={() => setNotes('')}
                className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-elevated niuu:px-2.5 niuu:py-1 niuu:text-[11px] niuu:text-text-secondary niuu:cursor-pointer niuu:disabled:opacity-50"
              >
                Request changes
              </button>
            </div>
          ) : (
            <div className="niuu:flex niuu:flex-col niuu:gap-2">
              <textarea
                data-testid={`workflow-run-notes-${campaign.slug}`}
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                rows={2}
                placeholder="What should change?"
                aria-label="What should change?"
                className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-elevated niuu:px-2.5 niuu:py-1.5 niuu:text-[12px] niuu:text-text-primary"
              />
              <div className="niuu:flex niuu:items-center niuu:gap-2">
                <button
                  type="button"
                  data-testid={`workflow-run-send-${campaign.slug}`}
                  disabled={notes.trim().length === 0 || review.isPending}
                  onClick={() => decide('changes_requested', notes.trim())}
                  className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-elevated niuu:px-2.5 niuu:py-1 niuu:text-[11px] niuu:text-text-primary niuu:cursor-pointer niuu:disabled:opacity-50"
                >
                  Send
                </button>
                <button
                  type="button"
                  onClick={() => setNotes(null)}
                  className="niuu:bg-transparent niuu:border-none niuu:p-0 niuu:text-[11px] niuu:text-text-faint niuu:cursor-pointer"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      ) : null}

      {review.isError ? (
        <p className="niuu:m-0 niuu:pl-[18px] niuu:text-[11px] niuu:text-critical" role="alert">
          {review.error instanceof Error ? review.error.message : 'Review failed.'}
        </p>
      ) : null}
    </li>
  );
}

export interface WorkflowRunsCardProps {
  workflowId: string;
  className?: string;
}

export function WorkflowRunsCard({ workflowId, className }: WorkflowRunsCardProps) {
  const specs = useSpecCampaigns();
  const research = useResearchCampaigns();
  useCampaignEvents(CAMPAIGN_QUERY_KEYS);

  const runs: WorkflowRun[] = [
    ...(specs.data ?? []).map((campaign) => ({ campaign, surface: 'specs' as const })),
    ...(research.data ?? []).map((campaign) => ({ campaign, surface: 'research' as const })),
  ]
    .filter((run) => run.campaign.workflowId === workflowId)
    .sort(
      (left, right) =>
        new Date(right.campaign.updatedAt).getTime() - new Date(left.campaign.updatedAt).getTime(),
    );

  const loading = specs.isLoading || research.isLoading;
  const failure = specs.error ?? research.error;

  return (
    <section
      data-testid="workflow-runs-card"
      className={cn(
        'niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-5 niuu:font-sans',
        className,
      )}
    >
      <div
        role="heading"
        aria-level={2}
        className="niuu:mb-1 niuu:text-[15px] niuu:font-semibold niuu:text-text-primary"
      >
        Runs of this workflow
      </div>
      <p className="niuu:m-0 niuu:mb-3 niuu:text-[11px] niuu:text-text-muted">
        Campaign runs. A launch you start here opens as a session instead.
      </p>

      {loading ? <LoadingState label="Loading runs…" /> : null}
      {failure ? (
        <p className="niuu:m-0 niuu:text-[12px] niuu:text-critical" role="alert">
          {failure instanceof Error ? failure.message : 'Failed to load runs.'}
        </p>
      ) : null}
      {!loading && !failure && runs.length === 0 ? (
        <p className="niuu:m-0 niuu:text-[12px] niuu:text-text-muted">
          No runs recorded for this workflow yet.
        </p>
      ) : null}
      {runs.length > 0 ? (
        <ul className="niuu:m-0 niuu:list-none niuu:p-0">
          {runs.map((run) => (
            <RunRow key={`${run.surface}-${run.campaign.id}`} run={run} />
          ))}
        </ul>
      ) : null}
    </section>
  );
}
