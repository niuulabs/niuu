import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { useQueryClient } from '@tanstack/react-query';
import { openEventStream } from '@niuulabs/query';
import { Table, type TableColumn } from '@niuulabs/ui';
import type { CampaignArtifactSummary, ResearchCampaign } from '../ports';
import { useResearchCampaigns } from './useResearch';
import { useDispatcherState } from './useDispatcherState';
import './ResearchCenterPage.css';

type ResearchFilter = 'all' | 'needs-attention' | 'running' | 'published' | 'drafts';
type ResearchSort = 'updated' | 'created' | 'confidence';
type ResearchViewMode = 'grid' | 'table';
type CampaignBucket = 'running' | 'review-ready' | 'blocked' | 'failed' | 'published' | 'draft';
type ConfidenceTier = 'high' | 'med' | 'low';

interface CampaignCardViewModel {
  id: string;
  slug: string;
  name: string;
  question: string;
  bucket: CampaignBucket;
  bucketLabel: string;
  modeLabel: string;
  modeTone: string;
  confidence: number;
  confidenceTier: ConfidenceTier;
  progressPercent: number;
  progressSegments: Array<{ key: string; status: string }>;
  elapsedLabel: string;
  sourceCount: number;
  critiqueCount: number;
  learningCount: number;
  followUpCount: number;
  warning: string | null;
  runLabel: string;
  updatedAt: string;
  createdAt: string;
  metadataQuestion: string;
}

const FILTER_LABELS: Record<ResearchFilter, string> = {
  all: 'All',
  'needs-attention': 'Needs attention',
  running: 'Running',
  published: 'Published',
  drafts: 'Drafts',
};

const SORT_LABELS: Record<ResearchSort, string> = {
  updated: 'Updated',
  created: 'Created',
  confidence: 'Confidence',
};

function pageStatusLabel(bucket: CampaignBucket): string {
  switch (bucket) {
    case 'review-ready':
      return 'REVIEW-READY';
    case 'blocked':
      return 'BLOCKED';
    case 'failed':
      return 'FAILED';
    case 'published':
      return 'PUBLISHED';
    case 'draft':
      return 'DRAFT';
    default:
      return 'RUNNING';
  }
}

function bucketForCampaign(
  campaign: ResearchCampaign,
  summary: CampaignArtifactSummary | null,
): CampaignBucket {
  if (campaign.status === 'failed') return 'failed';
  if (campaign.status === 'blocked') return 'blocked';
  if (summary?.published) return 'published';
  if (campaign.status === 'running') return 'running';
  if (campaign.status === 'pending') return 'draft';
  return 'review-ready';
}

function normalizeMode(mode: unknown): string {
  if (typeof mode !== 'string' || !mode.trim()) return 'exploratory';
  return mode.trim().toLowerCase();
}

function modeTone(mode: string): string {
  if (mode === 'investigative') return 'investigative';
  if (mode === 'evaluative') return 'evaluative';
  if (mode === 'monitoring') return 'monitoring';
  return 'exploratory';
}

function formatModeLabel(mode: string): string {
  return mode.replace(/[-_]/g, ' ').toUpperCase();
}

function buildQuestion(campaign: ResearchCampaign): string {
  const question = campaign.metadata.question;
  if (typeof question === 'string' && question.trim()) return question.trim();
  return 'Review campaign artifacts and published memory in Mimir.';
}

function deriveWarning(
  campaign: ResearchCampaign,
  summary: CampaignArtifactSummary | null,
  bucket: CampaignBucket,
): string | null {
  const firstReason = campaign.stageState.find((stage) => stage.reason)?.reason?.trim();
  if (firstReason) return firstReason;
  if (bucket === 'failed') return 'workflow run failed before publish';
  if (bucket === 'blocked') return 'campaign needs human review before it can continue';
  // Unknown, not empty: Mímir could not be read, so say so rather than imply
  // the campaign has produced nothing.
  if (summary && !summary.known) return 'artifact store unreachable; counts unknown';
  if (!summary && campaign.status === 'running')
    return 'awaiting artifact updates from live workflow';
  return null;
}

function normalizeSegments(campaign: ResearchCampaign, bucket: CampaignBucket) {
  const baseSegments =
    campaign.stageState.length > 0
      ? campaign.stageState
      : [{ stageId: 'workflow-launch', label: 'Workflow launch', status: campaign.status }];
  return baseSegments.map((stage, index) => ({
    key: `${stage.stageId}-${index}`,
    status:
      bucket === 'published' || bucket === 'review-ready'
        ? 'complete'
        : stage.status === 'gated'
          ? 'blocked'
          : stage.status,
  }));
}

function deriveProgressPercent(campaign: ResearchCampaign, bucket: CampaignBucket): number {
  if (bucket === 'published') return 100;
  if (bucket === 'review-ready') return 78;
  if (campaign.stageState.length === 0) {
    if (campaign.status === 'running') return 18;
    if (campaign.status === 'pending') return 8;
    return 0;
  }
  const complete = campaign.stageState.filter((stage) => stage.status === 'complete').length;
  const active = campaign.stageState.some((stage) => stage.status === 'active') ? 0.5 : 0;
  const blocked = campaign.stageState.some((stage) => stage.status === 'blocked') ? 0.15 : 0;
  return Math.max(
    8,
    Math.min(96, Math.round(((complete + active + blocked) / campaign.stageState.length) * 100)),
  );
}

function deriveConfidence(
  campaign: ResearchCampaign,
  summary: CampaignArtifactSummary | null,
  bucket: CampaignBucket,
  progressPercent: number,
): number {
  const artifactCount = summary?.artifactCount ?? 0;
  const sourceCount = summary?.sourceCount ?? 0;
  let score =
    24 + Math.round(progressPercent * 0.36) + artifactCount * 6 + Math.min(sourceCount, 6);
  if (bucket === 'review-ready') score = Math.max(score, 78);
  if (bucket === 'published') score = Math.max(score, 91);
  if (bucket === 'blocked') score = Math.min(score, 62);
  if (bucket === 'failed') score = Math.min(score, 55);
  if (campaign.status === 'pending') score = Math.min(score, 35);
  return Math.max(18, Math.min(96, score));
}

function confidenceTier(value: number): ConfidenceTier {
  if (value >= 80) return 'high';
  if (value >= 60) return 'med';
  return 'low';
}

function formatRelativeDuration(startIso: string, endIso: string): string {
  const start = new Date(startIso).getTime();
  const end = new Date(endIso).getTime();
  const diff = Math.max(0, Math.round((end - start) / 1000));
  if (diff < 60) return `${diff}s`;
  const minutes = Math.floor(diff / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  if (days > 0) return `${days}d ${hours % 24}h`;
  if (hours > 0) return `${hours}h ${minutes % 60}m`;
  return `${minutes}m`;
}

function shortRunLabel(campaign: ResearchCampaign): string {
  return `run/${campaign.sessionId.slice(0, 8)}`;
}

function toViewModel(campaign: ResearchCampaign): CampaignCardViewModel {
  const summary = campaign.artifactSummary ?? null;
  const bucket = bucketForCampaign(campaign, summary);
  const mode = normalizeMode(campaign.metadata.mode);
  const progressPercent = deriveProgressPercent(campaign, bucket);
  const confidence = deriveConfidence(campaign, summary, bucket, progressPercent);
  return {
    id: campaign.id,
    slug: campaign.slug,
    name: campaign.name,
    question: buildQuestion(campaign),
    bucket,
    bucketLabel: pageStatusLabel(bucket),
    modeLabel: formatModeLabel(mode),
    modeTone: modeTone(mode),
    confidence,
    confidenceTier: confidenceTier(confidence),
    progressPercent,
    progressSegments: normalizeSegments(campaign, bucket),
    elapsedLabel: formatRelativeDuration(campaign.createdAt, campaign.updatedAt),
    sourceCount: summary?.sourceCount ?? 0,
    critiqueCount: summary?.critiqueCount ?? 0,
    learningCount: summary?.learningCount ?? 0,
    followUpCount: summary?.followUpCount ?? 0,
    warning: deriveWarning(campaign, summary, bucket),
    runLabel: shortRunLabel(campaign),
    updatedAt: campaign.updatedAt,
    createdAt: campaign.createdAt,
    metadataQuestion: buildQuestion(campaign),
  };
}

function filterMatches(
  card: CampaignCardViewModel,
  query: string,
  filter: ResearchFilter,
): boolean {
  const normalizedQuery = query.trim().toLowerCase();
  if (normalizedQuery) {
    const haystack = [card.name, card.slug, card.question, card.modeLabel, card.runLabel]
      .join(' ')
      .toLowerCase();
    if (!haystack.includes(normalizedQuery)) return false;
  }
  if (filter === 'all') return true;
  if (filter === 'needs-attention') return card.bucket === 'blocked' || card.bucket === 'failed';
  if (filter === 'running') return card.bucket === 'running';
  if (filter === 'published') return card.bucket === 'published';
  return card.bucket === 'draft';
}

function sortCards(cards: CampaignCardViewModel[], sortBy: ResearchSort): CampaignCardViewModel[] {
  const sorted = [...cards];
  sorted.sort((a, b) => {
    if (sortBy === 'confidence') return b.confidence - a.confidence;
    if (sortBy === 'created')
      return new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime();
    return new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime();
  });
  return sorted;
}

function bucketCount(
  cards: CampaignCardViewModel[],
  bucket: CampaignBucket | CampaignBucket[],
): number {
  const set = new Set(Array.isArray(bucket) ? bucket : [bucket]);
  return cards.filter((card) => set.has(card.bucket)).length;
}

function summaryCards(cards: CampaignCardViewModel[]) {
  return [
    { key: 'running', label: 'RUNNING', value: bucketCount(cards, 'running'), sub: 'active runs' },
    {
      key: 'review-ready',
      label: 'REVIEW-READY',
      value: bucketCount(cards, 'review-ready'),
      sub: 'awaiting publish',
    },
    {
      key: 'blocked',
      label: 'BLOCKED / FAILED',
      value: bucketCount(cards, ['blocked', 'failed']),
      sub: 'need attention',
      tone: 'alert',
    },
    {
      key: 'published',
      label: 'PUBLISHED',
      value: bucketCount(cards, 'published'),
      sub: 'in mimir',
    },
    { key: 'draft', label: 'DRAFTS', value: bucketCount(cards, 'draft'), sub: 'not dispatched' },
  ];
}

function ResearchFilterChip({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={['research-filter-chip', active ? 'is-active' : ''].join(' ').trim()}
    >
      <span>{label}</span>
      <span className="research-filter-chip__count">{count}</span>
    </button>
  );
}

function ViewModeToggle({
  value,
  onChange,
}: {
  value: ResearchViewMode;
  onChange: (next: ResearchViewMode) => void;
}) {
  return (
    <div className="research-view-toggle" role="tablist" aria-label="Display mode">
      {(['grid', 'table'] as const).map((mode) => (
        <button
          key={mode}
          type="button"
          role="tab"
          aria-selected={value === mode}
          onClick={() => onChange(mode)}
          className={['research-view-toggle__button', value === mode ? 'is-active' : '']
            .join(' ')
            .trim()}
        >
          {mode === 'grid' ? 'Grid' : 'Table'}
        </button>
      ))}
    </div>
  );
}

function ResearchSummaryMetric({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: number;
  sub: string;
  tone?: string;
}) {
  return (
    <article className={['research-summary-metric', tone ? `is-${tone}` : ''].join(' ').trim()}>
      <div className="research-summary-metric__label">{label}</div>
      <div className="research-summary-metric__value">{value}</div>
      <div className="research-summary-metric__sub">{sub}</div>
    </article>
  );
}

function ProgressBar({ segments }: { segments: Array<{ key: string; status: string }> }) {
  return (
    <div className="research-progress" aria-hidden="true">
      {segments.map((segment) => (
        <span
          key={segment.key}
          className={['research-progress__segment', `is-${segment.status}`].join(' ')}
        />
      ))}
    </div>
  );
}

function ResearchCampaignCard({
  card,
  onOpen,
}: {
  card: CampaignCardViewModel;
  onOpen: () => void;
}) {
  return (
    <button type="button" onClick={onOpen} className="research-campaign-card">
      <div className="research-campaign-card__topline">
        <div className="research-campaign-card__statusline">
          <span className={['research-dot', `is-${card.bucket}`].join(' ')} />
          <span className="research-campaign-card__status">{card.bucketLabel}</span>
          <span className={['research-mode-pill', `is-${card.modeTone}`].join(' ')}>
            {card.modeLabel}
          </span>
        </div>
        <div className="research-campaign-card__confidence">
          <span className="research-campaign-card__confidence-bar" />
          <span>{card.confidence}%</span>
          <span className="research-campaign-card__confidence-tier">{card.confidenceTier}</span>
        </div>
      </div>

      <h2 className="research-campaign-card__title">{card.name}</h2>
      <p className="research-campaign-card__question">{card.question}</p>

      <ProgressBar segments={card.progressSegments} />

      <div className="research-campaign-card__metrics">
        <span>{card.elapsedLabel}</span>
        <span>{card.sourceCount} src</span>
        <span>{card.critiqueCount} crit</span>
        <span>{card.learningCount} learn</span>
        <span>{card.followUpCount} f/u</span>
      </div>

      <div className="research-campaign-card__warning-row">
        {card.warning ? (
          <p className={['research-campaign-card__warning', `is-${card.bucket}`].join(' ')}>
            {card.warning}
          </p>
        ) : (
          <span />
        )}
      </div>

      <div className="research-campaign-card__footer">
        <span className="research-campaign-card__slug">{card.slug}</span>
        <span className="research-campaign-card__run">{card.runLabel}</span>
      </div>
    </button>
  );
}

function ResearchTableView({
  rows,
  onOpen,
}: {
  rows: CampaignCardViewModel[];
  onOpen: (slug: string) => void;
}) {
  const columns: TableColumn<CampaignCardViewModel>[] = [
    {
      key: 'campaign',
      header: 'Campaign',
      sortable: true,
      render: (row) => (
        <div className="research-table__campaign">
          <button type="button" onClick={() => onOpen(row.slug)} className="research-table__link">
            {row.name}
          </button>
          <div className="research-table__sub">{row.slug}</div>
        </div>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      sortable: true,
      width: '170px',
      render: (row) => (
        <div className="research-table__status">
          <span className={['research-dot', `is-${row.bucket}`].join(' ')} />
          <span>{row.bucketLabel}</span>
        </div>
      ),
    },
    {
      key: 'mode',
      header: 'Mode',
      sortable: true,
      width: '160px',
      render: (row) => <span className="research-table__mono">{row.modeLabel}</span>,
    },
    {
      key: 'confidence',
      header: 'Confidence',
      sortable: true,
      width: '120px',
      render: (row) => (
        <span className="research-table__mono">
          {row.confidence}% · {row.confidenceTier}
        </span>
      ),
    },
    {
      key: 'updatedAt',
      header: 'Updated',
      sortable: true,
      width: '160px',
      render: (row) => <span className="research-table__mono">{row.elapsedLabel}</span>,
    },
  ];

  return (
    <div className="research-table-shell">
      <Table columns={columns} rows={rows} aria-label="Research campaigns" />
    </div>
  );
}

export function ResearchCenterPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: campaigns = [], isLoading, isError, error } = useResearchCampaigns();
  const { data: dispatcherState } = useDispatcherState();
  const [search, setSearch] = useState('');
  const [activeFilter, setActiveFilter] = useState<ResearchFilter>('all');
  const [sortBy, setSortBy] = useState<ResearchSort>('updated');
  const [viewMode, setViewMode] = useState<ResearchViewMode>('grid');

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const stream = openEventStream('/api/v1/ting/events', {
      onMessage: () => {},
      onEvent: ({ event }) => {
        if (
          event === 'workflow.campaign.created' ||
          event === 'workflow.campaign.updated' ||
          event === 'workflow.campaign.completed' ||
          event === 'workflow.campaign.failed'
        ) {
          void queryClient.invalidateQueries({ queryKey: ['ting', 'research', 'campaigns'] });
        }
      },
    });
    return () => stream.close();
  }, [queryClient]);

  const cards = useMemo(() => campaigns.map(toViewModel), [campaigns]);

  const counts = useMemo(
    () => ({
      all: cards.length,
      'needs-attention': bucketCount(cards, ['blocked', 'failed']),
      running: bucketCount(cards, 'running'),
      published: bucketCount(cards, 'published'),
      drafts: bucketCount(cards, 'draft'),
    }),
    [cards],
  );

  const visibleCards = useMemo(() => {
    return sortCards(
      cards.filter((card) => filterMatches(card, search, activeFilter)),
      sortBy,
    );
  }, [activeFilter, cards, search, sortBy]);

  const summary = useMemo(() => summaryCards(cards), [cards]);

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
                placeholder="search by title, slug, question"
                aria-label="Search campaigns"
              />
              <span className="research-search__kbd">⌘K</span>
            </label>

            <div className="research-filter-row">
              {(['all', 'needs-attention', 'running', 'published', 'drafts'] as const).map(
                (filter) => (
                  <ResearchFilterChip
                    key={filter}
                    label={FILTER_LABELS[filter]}
                    count={counts[filter]}
                    active={activeFilter === filter}
                    onClick={() => setActiveFilter(filter)}
                  />
                ),
              )}
            </div>
          </div>

          <div className="research-center-toolbar__right">
            <ViewModeToggle value={viewMode} onChange={setViewMode} />
            <button
              type="button"
              onClick={() => navigate({ to: '/ting/research/new' })}
              className="research-new-button"
            >
              + New campaign
            </button>
          </div>
        </section>

        <div className="research-center-divider" />

        <section className="research-summary-grid" aria-label="Research campaign summary">
          {summary.map((card) => (
            <ResearchSummaryMetric
              key={card.key}
              label={card.label}
              value={card.value}
              sub={card.sub}
              tone={card.tone}
            />
          ))}
        </section>

        <section className="research-section-heading">
          <div className="research-section-heading__title">
            ALL CAMPAIGNS • {visibleCards.length}
          </div>
          <div className="research-sort-row">
            <span className="research-sort-row__label">SORT:</span>
            {(['updated', 'created', 'confidence'] as const).map((sort) => (
              <button
                key={sort}
                type="button"
                onClick={() => setSortBy(sort)}
                className={['research-sort-chip', sortBy === sort ? 'is-active' : '']
                  .join(' ')
                  .trim()}
              >
                {SORT_LABELS[sort]}
              </button>
            ))}
          </div>
        </section>

        {isLoading ? <section className="research-empty-state">Loading campaigns…</section> : null}

        {isError ? (
          <section className="research-empty-state is-error">
            {error instanceof Error ? error.message : 'Failed to load campaigns.'}
          </section>
        ) : null}

        {!isLoading && !isError && visibleCards.length === 0 ? (
          <section className="research-empty-state">
            No campaigns match the current filters.
          </section>
        ) : null}

        {!isLoading && !isError && visibleCards.length > 0 ? (
          viewMode === 'grid' ? (
            <section className="research-card-grid">
              {visibleCards.map((card) => (
                <ResearchCampaignCard
                  key={card.id}
                  card={card}
                  onOpen={() =>
                    navigate({ to: '/ting/research/$slug', params: { slug: card.slug } })
                  }
                />
              ))}
            </section>
          ) : (
            <ResearchTableView
              rows={visibleCards}
              onOpen={(slug) => navigate({ to: '/ting/research/$slug', params: { slug } })}
            />
          )
        ) : null}

        <footer className="research-bottom-rail">
          <div className="research-bottom-rail__side">
            <span>ting</span>
            <span className="research-bottom-rail__sep">·</span>
            <span>route /ting/research</span>
            <span className="research-bottom-rail__sep">·</span>
            <span>dispatcher {dispatcherState?.running ? 'live' : 'paused'}</span>
          </div>
          <div className="research-bottom-rail__side">
            <span>workflow campaigns</span>
            <span className="research-bottom-rail__sep">·</span>
            <span>research-campaign</span>
            <span className="research-bottom-rail__sep">·</span>
            <span>connected via mimir</span>
          </div>
        </footer>
      </div>
    </div>
  );
}
