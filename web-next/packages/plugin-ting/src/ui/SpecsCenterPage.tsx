import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { useQueryClient } from '@tanstack/react-query';
import { openEventStream } from '@niuulabs/query';
import type { SpecCampaign } from '../ports';
import { useSpecCampaigns } from './useSpecs';
import './ResearchCenterPage.css';
import './SpecsPage.css';

type SpecFilter = 'all' | 'review' | 'running' | 'published' | 'draft';

const FILTER_LABELS: Record<SpecFilter, string> = {
  all: 'All',
  review: 'Review required',
  running: 'Running',
  published: 'Published',
  draft: 'Draft',
};

const DOCS = [
  ['prd', 'PRD'],
  ['srd', 'SRD'],
  ['sdd', 'SDD'],
  ['breakdown', 'Breakdown'],
] as const;

interface PendingSpecGate {
  id: string;
  nodeId: string;
  summary: string;
  instructions: string;
}

function pendingSpecGates(campaign: SpecCampaign): PendingSpecGate[] {
  const raw = campaign.metadata.pending_workflow_gates;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((gate): gate is Record<string, unknown> => typeof gate === 'object' && gate !== null)
    .map((gate) => ({
      id: String(gate.id ?? gate.gate_id ?? gate.gateId ?? ''),
      nodeId: String(gate.node_id ?? gate.nodeId ?? ''),
      summary: String(gate.summary ?? gate.label ?? 'Review required'),
      instructions: String(gate.instructions ?? ''),
    }))
    .filter((gate) => gate.nodeId.startsWith('spec-') && gate.nodeId.endsWith('-gate'));
}

function statusForCampaign(campaign: SpecCampaign): 'review' | 'running' | 'published' | 'draft' {
  if (pendingSpecGates(campaign).length > 0 || campaign.status === 'blocked') return 'review';
  if (campaign.status === 'completed') return 'published';
  if (campaign.status === 'pending') return 'draft';
  return 'running';
}

function campaignPrompt(campaign: SpecCampaign): string {
  const prompt = campaign.metadata.prompt;
  if (typeof prompt === 'string' && prompt.trim()) return prompt.trim();
  return 'Specification campaign';
}

function artifactKinds(campaign: SpecCampaign): Set<string> {
  const canonical = campaign.metadata.canonical_artifacts;
  if (canonical && typeof canonical === 'object') return new Set(Object.keys(canonical));
  return new Set(campaign.stageState.map((stage) => stage.stageId.replace(/^spec-/, '')));
}

function progressPercent(campaign: SpecCampaign): number {
  const stages = campaign.stageState;
  if (!stages.length) return campaign.status === 'pending' ? 8 : 18;
  const complete = stages.filter((stage) => stage.status === 'complete').length;
  const active = stages.some((stage) => stage.status === 'active') ? 0.5 : 0;
  const blocked = stages.some((stage) => stage.status === 'blocked') ? 0.2 : 0;
  return Math.max(
    8,
    Math.min(100, Math.round(((complete + active + blocked) / stages.length) * 100)),
  );
}

function activeStageLabel(campaign: SpecCampaign): string {
  const stages = campaign.stageState;
  const active =
    stages.find((stage) => stage.status === 'blocked' || stage.status === 'active') ??
    stages.find((stage) => stage.stageId === campaign.activeStageId) ??
    stages.find((stage) => stage.status === 'pending') ??
    stages.at(-1);
  if (!active) return campaign.status === 'pending' ? 'Queued' : 'Starting';
  return active.label || active.stageId.replace(/^spec-/, '').replace(/-/g, ' ');
}

function filterMatches(campaign: SpecCampaign, query: string, filter: SpecFilter): boolean {
  const status = statusForCampaign(campaign);
  if (filter !== 'all' && status !== filter) return false;
  const normalized = query.trim().toLowerCase();
  if (!normalized) return true;
  return [campaign.name, campaign.slug, campaignPrompt(campaign)]
    .join(' ')
    .toLowerCase()
    .includes(normalized);
}

function countStatus(campaigns: SpecCampaign[], status: SpecFilter): number {
  if (status === 'all') return campaigns.length;
  return campaigns.filter((campaign) => statusForCampaign(campaign) === status).length;
}

function SpecCard({ campaign, onOpen }: { campaign: SpecCampaign; onOpen: () => void }) {
  const gates = pendingSpecGates(campaign);
  const status = statusForCampaign(campaign);
  const kinds = artifactKinds(campaign);
  const progress = progressPercent(campaign);
  const stage = activeStageLabel(campaign);
  return (
    <button type="button" onClick={onOpen} className="research-campaign-card specs-campaign-card">
      <div className="research-campaign-card__topline">
        <div className="research-campaign-card__statusline">
          <span
            className={[
              'research-dot',
              status === 'review'
                ? 'is-blocked'
                : status === 'published'
                  ? 'is-published'
                  : 'is-running',
            ].join(' ')}
          />
          <span className="research-campaign-card__status">
            {status === 'review' ? 'REVIEW REQUIRED' : status.toUpperCase()}
          </span>
          {gates[0] ? <span className="specs-review-pill">{gates[0].summary}</span> : null}
        </div>
        <div className="research-campaign-card__confidence">
          <span className="research-campaign-card__confidence-bar" />
          <span>{progress}%</span>
        </div>
      </div>

      <h2 className="research-campaign-card__title">{campaign.name}</h2>
      <div className="specs-campaign-card__stage">
        <span>Stage</span>
        <strong>{stage}</strong>
      </div>
      <p className="research-campaign-card__question">{campaignPrompt(campaign)}</p>

      <div className="research-progress" aria-hidden="true">
        {campaign.stageState.length > 0
          ? campaign.stageState.map((stage) => (
              <span
                key={stage.stageId}
                className={['research-progress__segment', `is-${stage.status}`].join(' ')}
              />
            ))
          : null}
      </div>

      <div className="specs-campaign-card__doc-row">
        {DOCS.map(([kind, label]) => (
          <span
            key={kind}
            className={['specs-doc-pill', kinds.has(kind) ? 'is-present' : ''].join(' ').trim()}
          >
            {label}
          </span>
        ))}
      </div>

      <div className="research-campaign-card__footer">
        <span className="research-campaign-card__slug">{campaign.slug}</span>
        <span className="research-campaign-card__run">{campaign.sessionId.slice(0, 10)}</span>
      </div>
    </button>
  );
}

export function SpecsCenterPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: campaigns = [], isLoading, isError, error } = useSpecCampaigns();
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<SpecFilter>('all');

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const stream = openEventStream('/api/v1/ting/events', {
      onMessage: () => {},
      onEvent: ({ event }) => {
        if (event?.startsWith('workflow.campaign.')) {
          void queryClient.invalidateQueries({ queryKey: ['ting', 'specs', 'campaigns'] });
        }
      },
    });
    return () => stream.close();
  }, [queryClient]);

  const visibleCampaigns = useMemo(
    () =>
      campaigns
        .filter((campaign) => filterMatches(campaign, search, filter))
        .sort(
          (left, right) => new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime(),
        ),
    [campaigns, filter, search],
  );

  return (
    <div className="research-center-page">
      <div className="research-center-shell">
        <section className="research-center-toolbar">
          <div className="research-center-toolbar__left">
            <label className="research-search">
              <span className="research-search__icon">⌕</span>
              <input
                type="search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="search specs"
                aria-label="Search specs"
              />
              <span className="research-search__kbd">⌘K</span>
            </label>
            <div className="research-filter-row">
              {(['all', 'review', 'running', 'published', 'draft'] as const).map((nextFilter) => (
                <button
                  key={nextFilter}
                  type="button"
                  onClick={() => setFilter(nextFilter)}
                  className={[
                    'research-filter-chip',
                    filter === nextFilter ? 'is-active' : '',
                  ].join(' ')}
                >
                  <span>{FILTER_LABELS[nextFilter]}</span>
                  <span className="research-filter-chip__count">
                    {countStatus(campaigns, nextFilter)}
                  </span>
                </button>
              ))}
            </div>
          </div>
          <div className="research-center-toolbar__right">
            <button
              type="button"
              onClick={() => navigate({ to: '/ting/specs/new' })}
              className="research-new-button"
            >
              + New spec
            </button>
          </div>
        </section>

        <div className="research-center-divider" />

        <section className="research-section-heading">
          <div className="research-section-heading__title">
            SPEC CAMPAIGNS • {visibleCampaigns.length}
          </div>
        </section>

        {isLoading ? <section className="research-empty-state">Loading specs…</section> : null}
        {isError ? (
          <section className="research-empty-state is-error">
            {error instanceof Error ? error.message : 'Failed to load specs.'}
          </section>
        ) : null}
        {!isLoading && !isError && visibleCampaigns.length === 0 ? (
          <section className="research-empty-state">No specs match the current filters.</section>
        ) : null}
        {!isLoading && !isError && visibleCampaigns.length > 0 ? (
          <section className="specs-card-grid">
            {visibleCampaigns.map((campaign) => (
              <SpecCard
                key={campaign.id}
                campaign={campaign}
                onOpen={() =>
                  navigate({ to: '/ting/specs/$slug', params: { slug: campaign.slug } })
                }
              />
            ))}
          </section>
        ) : null}
      </div>
    </div>
  );
}
