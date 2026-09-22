import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearch } from '@tanstack/react-router';
import { useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  ChevronRight,
  CircleDot,
  ExternalLink,
  FileText,
  GitBranch,
  LoaderCircle,
  Plus,
  Search,
  TicketCheck,
} from 'lucide-react';
import { EmptyState, ErrorState, LoadingState, Modal, relTime } from '@niuulabs/ui';
import type { IDispatchBus } from '../ports';
import type {
  SourceCoverage,
  WorkCollection,
  WorkDetail,
  WorkResourceKind,
  WorkSummary,
  WorkTask,
} from '../domain/work';
import { mergeWorkPages, useWorkCollection, useWorkDetail } from './useWork';
import { TrackerWorkImportDialog } from './TrackerWorkImportDialog';
import { WorkCampaignResults } from './WorkCampaignResults';
import './WorkPage.css';

type WorkFilter = 'overview' | 'attention' | 'active' | 'completed';
type WorkCondition = WorkSummary['condition'];

const CONDITION_LABEL: Record<WorkCondition, string> = {
  needsAttention: 'Needs attention',
  active: 'In progress',
  waiting: 'Waiting',
  completed: 'Completed',
  failed: 'Failed',
  unknown: 'Unknown',
};

const KIND_LABEL: Record<WorkSummary['kind'], string> = {
  project: 'Connected project',
  research: 'Research',
  specification: 'Specification',
  planning: 'Plan',
  workflowLaunch: 'Workflow',
  workflowExecution: 'Workflow execution',
};

const FILTER_LABEL: Record<WorkFilter, string> = {
  overview: 'Overview',
  attention: 'Needs attention',
  active: 'Active',
  completed: 'Completed',
};

export function workRouteId(item: Pick<WorkSummary, 'id'>): string {
  return item.id;
}

export function parseWorkRouteId(
  value: string | undefined,
): { kind: WorkResourceKind; id: string } | null {
  if (!value) return null;
  const separator = value.indexOf(':');
  if (separator <= 0 || separator === value.length - 1) return null;
  const kind = value.slice(0, separator);
  if (kind !== 'project' && kind !== 'campaign' && kind !== 'execution') return null;
  return { kind, id: value.slice(separator + 1) };
}

function summaryBackendKind(item: WorkSummary): WorkResourceKind {
  if (item.kind === 'project') return 'project';
  if (item.kind === 'workflowExecution') return 'execution';
  return 'campaign';
}

function conditionTone(condition: WorkCondition): string {
  if (condition === 'needsAttention' || condition === 'failed') return 'attention';
  if (condition === 'completed') return 'complete';
  if (condition === 'active') return 'active';
  return 'quiet';
}

function workMatchesFilter(item: WorkSummary, filter: WorkFilter): boolean {
  if (filter === 'overview') return true;
  if (filter === 'attention') {
    return item.condition === 'needsAttention' || item.condition === 'failed';
  }
  if (filter === 'active') {
    return ['active', 'waiting', 'unknown'].includes(item.condition);
  }
  return item.condition === 'completed';
}

function workMatchesSearch(item: WorkSummary, search: string): boolean {
  if (!search.trim()) return true;
  const source = item.source;
  const haystack = [
    item.title,
    item.currentStep,
    item.rawState.source,
    item.rawState.status,
    source?.provider,
    source?.projectId,
    item.workflow?.id,
  ]
    .filter(Boolean)
    .join(' ')
    .toLocaleLowerCase();
  return haystack.includes(search.trim().toLocaleLowerCase());
}

function WorkConditionBadge({ condition }: { condition: WorkCondition }) {
  return (
    <span className={`ting-work-condition ting-work-condition--${conditionTone(condition)}`}>
      <span aria-hidden="true" />
      {CONDITION_LABEL[condition]}
    </span>
  );
}

function WorkRow({ item, onSelect }: { item: WorkSummary; onSelect: () => void }) {
  const updated = item.updatedAt ? relTime(item.updatedAt) : null;
  return (
    <button
      type="button"
      className="ting-work-row"
      onClick={onSelect}
      data-testid={`work-row-${item.id}`}
    >
      <span className="ting-work-row__copy">
        <strong>{item.title}</strong>
        <span>
          {KIND_LABEL[item.kind]}
          {item.source?.projectId ? ` · ${item.source.projectId}` : ''}
          {item.currentStep ? ` · ${item.currentStep}` : ''}
        </span>
      </span>
      <WorkConditionBadge condition={item.condition} />
      <span
        className="ting-work-row__time"
        aria-label={updated ? `Updated ${updated}` : 'Update time unavailable'}
      >
        {updated ?? '—'}
      </span>
      <ChevronRight aria-hidden="true" className="ting-work-row__arrow" />
    </button>
  );
}

function WorkSection({
  title,
  items,
  onSelect,
}: {
  title: string;
  items: WorkSummary[];
  onSelect: (item: WorkSummary) => void;
}) {
  if (items.length === 0) return null;
  return (
    <section className="ting-work-section" aria-labelledby={`work-section-${title}`}>
      <div className="ting-work-section__heading">
        <h2 id={`work-section-${title}`}>{title}</h2>
      </div>
      <div className="ting-work-list">
        {items.map((item) => (
          <WorkRow key={item.id} item={item} onSelect={() => onSelect(item)} />
        ))}
      </div>
    </section>
  );
}

function CoverageNotices({
  coverage,
  onRetry,
}: {
  coverage: SourceCoverage[];
  onRetry?: () => void;
}) {
  const incomplete = coverage.filter((entry) => entry.status !== 'complete');
  if (incomplete.length === 0) return null;
  return (
    <section className="ting-work-coverage" aria-label="Source coverage">
      {incomplete.map((entry) => (
        <div key={`${entry.source}:${entry.connectionId ?? ''}`} role="status">
          <AlertCircle aria-hidden="true" />
          <span>
            <strong>{entry.source}</strong>{' '}
            {entry.error?.message ??
              (entry.status === 'partial'
                ? 'returned partial results'
                : 'is currently unavailable')}
          </span>
          {onRetry ? (
            <button type="button" onClick={onRetry}>
              Retry
            </button>
          ) : null}
        </div>
      ))}
    </section>
  );
}

function NewWorkDialog({
  open,
  onOpenChange,
  onTracker,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onTracker: () => void;
}) {
  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Start new work"
      description="Begin from a tracker project or describe a new outcome."
      className="niuu:max-w-[680px]"
    >
      <div className="ting-new-work-options">
        <button
          type="button"
          onClick={() => {
            onOpenChange(false);
            onTracker();
          }}
          className="ting-new-work-option"
          data-testid="new-work-from-tracker"
        >
          <TicketCheck aria-hidden="true" />
          <span>
            <strong>From tracker</strong>
            <small>Choose a connected project, repositories, workflow and execution target.</small>
          </span>
          <ArrowRight aria-hidden="true" />
        </button>
        <div className="ting-new-work-option-group">
          <div className="ting-new-work-option ting-new-work-option--heading">
            <FileText aria-hidden="true" />
            <span>
              <strong>From a brief</strong>
              <small>Use the existing guided flow for the outcome you need.</small>
            </span>
          </div>
          <div className="ting-new-work-links">
            <Link to="/ting/plan" onClick={() => onOpenChange(false)}>
              Plan delivery
            </Link>
            <Link
              to="/ting/research/new"
              search={{ returnTo: '/ting/work' } as never}
              onClick={() => onOpenChange(false)}
            >
              Start research
            </Link>
            <Link
              to="/ting/specs/new"
              search={{ returnTo: '/ting/work' } as never}
              onClick={() => onOpenChange(false)}
            >
              Create specification
            </Link>
          </div>
        </div>
      </div>
    </Modal>
  );
}

function sourceLabel(item: WorkSummary): string | null {
  if (!item.source) return null;
  return [item.source.provider, item.source.projectId].filter(Boolean).join(' · ');
}

function specialistLabel(item: WorkSummary): string {
  if (item.kind === 'project') return 'Project setup';
  if (item.kind === 'specification' && item.actions.includes('review'))
    return 'Review specification';
  if (item.kind === 'research') return 'Open research';
  if (item.kind === 'planning') return 'Open plan';
  if (item.kind === 'workflowExecution') return 'Inspect execution';
  return 'Open details';
}

function WorkTaskRow({
  task,
  dispatching,
  operationalRunCoverageComplete,
  onDispatch,
}: {
  task: WorkTask;
  dispatching: boolean;
  operationalRunCoverageComplete: boolean;
  onDispatch: (task: WorkTask) => void;
}) {
  return (
    <article className="ting-work-task">
      <div className="ting-work-task__main">
        <a href={task.source.url} target="_blank" rel="noreferrer" className="ting-work-task__key">
          {task.source.identifier}
          <ExternalLink aria-hidden="true" />
        </a>
        <h3>{task.title}</h3>
        <div className="ting-work-task__states">
          <span>
            Tracker <strong>{task.source.status}</strong>
          </span>
          <span>
            Ting{' '}
            <strong>
              {task.operationalRun
                ? task.operationalRun.status
                : operationalRunCoverageComplete
                  ? 'Not started'
                  : 'Unknown · linkage unavailable'}
            </strong>
          </span>
        </div>
      </div>
      <div className="ting-work-task__actions">
        {task.operationalRun?.sessionId ? (
          <Link to={`/volundr/sessions/${task.operationalRun.sessionId}` as never}>
            Open session
          </Link>
        ) : null}
        {task.dispatch ? (
          <button type="button" disabled={dispatching} onClick={() => onDispatch(task)}>
            {dispatching ? 'Starting…' : 'Start eligible work'}
          </button>
        ) : null}
      </div>
    </article>
  );
}

function WorkDetailView({
  detail,
  onBack,
  onNew,
  onRefresh,
}: {
  detail: WorkDetail;
  onBack: () => void;
  onNew: () => void;
  onRefresh: () => Promise<unknown>;
}) {
  const dispatch = useService<IDispatchBus>('ting.dispatch');
  const queryClient = useQueryClient();
  const [dispatchingIssueId, setDispatchingIssueId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const item = detail.item;
  const normalizedCurrentStep = item.currentStep?.trim().toLocaleLowerCase() ?? '';
  const hasMeaningfulCurrentStep =
    Boolean(normalizedCurrentStep) &&
    normalizedCurrentStep !== item.rawState.status.trim().toLocaleLowerCase() &&
    normalizedCurrentStep !== CONDITION_LABEL[item.condition].toLocaleLowerCase();
  const hasCampaignResults =
    (item.kind === 'research' || item.kind === 'specification') && Boolean(item.campaignSlug);
  const operationalRunCoverageComplete = detail.coverage.some(
    (entry) => entry.source === 'operationalRuns' && entry.status === 'complete',
  );
  const trackerCoverageComplete = detail.coverage.some(
    (entry) => entry.source === 'tracker' && entry.status === 'complete',
  );

  async function handleDispatch(task: WorkTask) {
    if (!task.dispatch || dispatchingIssueId) return;
    setDispatchingIssueId(task.dispatch.issueId);
    setActionError(null);
    try {
      const results = await dispatch.approve(
        [
          {
            sagaId: task.dispatch.sagaId,
            issueId: task.dispatch.issueId,
            repo: task.dispatch.repo,
            ...(task.dispatch.connectionId ? { connectionId: task.dispatch.connectionId } : {}),
            ...(task.dispatch.workflowId ? { workflowId: task.dispatch.workflowId } : {}),
          },
        ],
        task.dispatch.connectionId ? { connectionId: task.dispatch.connectionId } : undefined,
      );
      const result = results[0];
      if (!result || result.status !== 'spawned') {
        throw new Error(
          result
            ? `Ting did not start this ticket (${result.status}). Review it in Operations.`
            : 'Ting did not return a dispatch result. Review the ticket in Operations.',
        );
      }
      await queryClient.invalidateQueries({ queryKey: ['ting', 'work'] });
      await onRefresh();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'Could not start this ticket');
    } finally {
      setDispatchingIssueId(null);
    }
  }

  return (
    <div className="ting-work-detail" data-testid="work-detail">
      <div className="ting-work-detail__nav">
        <button type="button" className="ting-work-back" aria-label="Back to work" onClick={onBack}>
          <ArrowLeft aria-hidden="true" /> Work
        </button>
        <button type="button" className="ting-work-new-secondary" onClick={onNew}>
          <Plus aria-hidden="true" /> New work
        </button>
      </div>
      <header className="ting-work-detail__header">
        <div>
          <span className="ting-work-eyebrow">{KIND_LABEL[item.kind]}</span>
          <h1>{item.title}</h1>
          <div className="ting-work-detail__context">
            <WorkConditionBadge condition={item.condition} />
            {sourceLabel(item) ? <span>{sourceLabel(item)}</span> : null}
            {item.workflow ? (
              <span>Workflow {item.workflow.version ?? 'version unavailable'}</span>
            ) : null}
          </div>
        </div>
        <div className="ting-work-detail__actions">
          {item.session && item.actions.includes('openSession') ? (
            <Link
              to={'/volundr/session/$sessionId' as never}
              params={{ sessionId: item.session.id } as never}
              search={
                {
                  instance_id: item.session.connectionId,
                  returnTo: `/ting/work/${encodeURIComponent(item.id)}`,
                } as never
              }
            >
              Open session
            </Link>
          ) : null}
          {item.workflow && item.actions.includes('openDefinition') ? (
            <Link
              to={'/ting/workflows/build' as never}
              search={{ id: item.workflow.id, version: item.workflow.version } as never}
            >
              Open definition
            </Link>
          ) : null}
          <Link to={item.links.specialist as never} className="ting-work-primary-link">
            {specialistLabel(item)} <ArrowRight aria-hidden="true" />
          </Link>
        </div>
      </header>

      <CoverageNotices coverage={detail.coverage} onRetry={() => void onRefresh()} />
      {actionError ? (
        <p className="ting-work-action-error" role="alert">
          {actionError}
        </p>
      ) : null}

      {item.attention ? (
        <section className={`ting-work-attention ting-work-attention--${item.attention.kind}`}>
          <span className="ting-work-eyebrow">
            {item.attention.kind === 'decision' ? 'Your decision' : 'Needs attention'}
          </span>
          <h2>{item.attention.message}</h2>
          <Link to={item.links.specialist as never}>{specialistLabel(item)}</Link>
        </section>
      ) : hasMeaningfulCurrentStep ? (
        <section className="ting-work-current-step">
          <span className="ting-work-eyebrow">Current step</span>
          <h2>{item.currentStep || CONDITION_LABEL[item.condition]}</h2>
          <p>
            Source state: <strong>{item.rawState.status || 'Unknown'}</strong>
          </p>
        </section>
      ) : null}

      {hasCampaignResults ? (
        <WorkCampaignResults
          key={item.id}
          kind={item.kind as 'research' | 'specification'}
          slug={item.campaignSlug!}
        />
      ) : null}

      {item.kind === 'project' ? (
        <section className="ting-work-tasks" aria-labelledby="project-tasks-title">
          <div className="ting-work-section__heading">
            <h2 id="project-tasks-title">Project tickets</h2>
            <span>{detail.tasks.length} tickets</span>
          </div>
          {detail.tasks.length > 0 ? (
            detail.tasks.map((task) => (
              <WorkTaskRow
                key={`${task.source.connectionId}:${task.source.issueId}`}
                task={task}
                dispatching={dispatchingIssueId === task.source.issueId}
                operationalRunCoverageComplete={operationalRunCoverageComplete}
                onDispatch={(selected) => void handleDispatch(selected)}
              />
            ))
          ) : trackerCoverageComplete ? (
            <EmptyState
              title="No tickets in this project"
              description="The connected source returned no tickets for this project."
            />
          ) : null}
        </section>
      ) : null}

      <details className="ting-work-facts">
        <summary>Provenance</summary>
        <dl>
          <div>
            <dt>Type</dt>
            <dd>{KIND_LABEL[item.kind]}</dd>
          </div>
          {item.source ? (
            <div>
              <dt>Source status</dt>
              <dd>{item.source.status || 'Unknown'}</dd>
            </div>
          ) : null}
          {item.workflow ? (
            <div>
              <dt>Workflow</dt>
              <dd>{item.workflow.version ?? 'Version unavailable'}</dd>
            </div>
          ) : null}
          {item.updatedAt ? (
            <div>
              <dt>Updated</dt>
              <dd>{relTime(item.updatedAt)}</dd>
            </div>
          ) : null}
        </dl>
        {item.links.source ? (
          <a href={item.links.source} target="_blank" rel="noreferrer">
            Open source <ExternalLink aria-hidden="true" />
          </a>
        ) : null}
      </details>
    </div>
  );
}

function WorkOverview({
  collection,
  filter,
  search,
  onFilter,
  onSearch,
  onSelect,
  onNew,
  onRetry,
  hasNextPage,
  loadingMore,
  onLoadMore,
}: {
  collection: WorkCollection;
  filter: WorkFilter;
  search: string;
  onFilter: (filter: WorkFilter) => void;
  onSearch: (search: string) => void;
  onSelect: (item: WorkSummary) => void;
  onNew: () => void;
  onRetry: () => void;
  hasNextPage: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
}) {
  const all = [...collection.projects, ...collection.campaigns, ...collection.executions];
  const visible = all.filter(
    (item) => workMatchesFilter(item, filter) && workMatchesSearch(item, search),
  );
  const needsAttention = visible.filter(
    (item) => item.condition === 'needsAttention' || item.condition === 'failed',
  );
  const active = visible.filter((item) =>
    ['active', 'waiting', 'unknown'].includes(item.condition),
  );
  const completed = visible.filter((item) => item.condition === 'completed');

  return (
    <main className="ting-work-overview" data-testid="work-overview">
      <header className="ting-work-overview__header">
        <h1>{FILTER_LABEL[filter]}</h1>
        <button type="button" className="ting-work-primary-action" onClick={onNew}>
          <Plus aria-hidden="true" /> New work
        </button>
      </header>

      <div className="ting-work-controls">
        <div className="ting-work-filters" aria-label="Work filters">
          {(Object.keys(FILTER_LABEL) as WorkFilter[]).map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={filter === value}
              onClick={() => onFilter(value)}
            >
              {FILTER_LABEL[value]}
            </button>
          ))}
        </div>
        <label className="ting-work-search">
          <Search aria-hidden="true" />
          <span className="niuu:sr-only">Search loaded work</span>
          <input
            type="search"
            value={search}
            onChange={(event) => onSearch(event.target.value)}
            placeholder="Search loaded work"
          />
        </label>
      </div>

      <CoverageNotices coverage={collection.coverage} onRetry={onRetry} />

      {collection.projects.length > 0 ? (
        <section className="ting-work-mobile-projects" aria-labelledby="mobile-projects-title">
          <div className="ting-work-section__heading">
            <h2 id="mobile-projects-title">Connected projects</h2>
          </div>
          {collection.projects.map((project) => (
            <WorkRow key={project.id} item={project} onSelect={() => onSelect(project)} />
          ))}
        </section>
      ) : null}

      {visible.length === 0 ? (
        <EmptyState
          title={all.length === 0 && !search ? 'No work yet' : 'No matching work'}
          description={
            search ? 'Try a different search or filter.' : 'Start work from a tracker or brief.'
          }
        />
      ) : filter === 'overview' ? (
        <>
          <WorkSection title="Needs attention" items={needsAttention} onSelect={onSelect} />
          <WorkSection title="Active" items={active} onSelect={onSelect} />
          <WorkSection title="Completed" items={completed} onSelect={onSelect} />
        </>
      ) : (
        <WorkSection title={FILTER_LABEL[filter]} items={visible} onSelect={onSelect} />
      )}

      {hasNextPage ? (
        <div className="ting-work-load-more">
          <button type="button" disabled={loadingMore} onClick={onLoadMore}>
            {loadingMore ? <LoaderCircle aria-hidden="true" /> : null}
            {loadingMore ? 'Loading…' : 'Load older executions'}
          </button>
          <span>More execution records are available.</span>
        </div>
      ) : null}
    </main>
  );
}

export function WorkPage() {
  const navigate = useNavigate();
  const pageQueryClient = useQueryClient();
  const params = useParams({ strict: false }) as { workId?: string };
  const routeIdentity = parseWorkRouteId(params.workId);
  const invalidRouteIdentity = Boolean(params.workId && !routeIdentity);
  const searchParams = useSearch({ strict: false }) as { filter?: string; q?: string };
  const filter = (['overview', 'attention', 'active', 'completed'] as const).includes(
    searchParams.filter as WorkFilter,
  )
    ? (searchParams.filter as WorkFilter)
    : 'overview';
  const search = typeof searchParams.q === 'string' ? searchParams.q : '';
  const [newWorkOpen, setNewWorkOpen] = useState(false);
  const [trackerImportOpen, setTrackerImportOpen] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);
  const listScrollPosition = useRef(0);
  const collectionQuery = useWorkCollection();
  const collection = useMemo(
    () => mergeWorkPages(collectionQuery.data?.pages),
    [collectionQuery.data?.pages],
  );
  const detailQuery = useWorkDetail(routeIdentity?.kind ?? null, routeIdentity?.id ?? null);
  const visibleCollection: WorkCollection = collection ?? {
    projects: [],
    campaigns: [],
    executions: [],
    executionNextCursor: null,
    coverage: [],
  };

  useEffect(() => {
    if (routeIdentity || !contentRef.current) return;
    contentRef.current.scrollTop = listScrollPosition.current;
  }, [routeIdentity]);

  function updateSearch(next: { filter?: WorkFilter; q?: string }) {
    void navigate({
      to: '/ting/work' as never,
      search: {
        ...(next.filter && next.filter !== 'overview' ? { filter: next.filter } : {}),
        ...(next.q ? { q: next.q } : {}),
      } as never,
    });
  }

  function selectWork(item: WorkSummary) {
    listScrollPosition.current = contentRef.current?.scrollTop ?? 0;
    const kind = summaryBackendKind(item);
    const expectedPrefix = `${kind}:`;
    const workId = item.id.startsWith(expectedPrefix) ? item.id : `${kind}:${item.id}`;
    void navigate({
      to: '/ting/work/$workId' as never,
      params: { workId } as never,
      search: {
        ...(filter !== 'overview' ? { filter } : {}),
        ...(search ? { q: search } : {}),
      } as never,
    });
  }

  if (invalidRouteIdentity) {
    return (
      <ErrorState
        title="Work link is invalid"
        message="This link does not contain a supported source-qualified work identity."
      />
    );
  }
  if (!routeIdentity && collectionQuery.isPending) return <LoadingState label="Loading work…" />;
  if (!routeIdentity && collectionQuery.isError) {
    return (
      <ErrorState
        title="Work could not be loaded"
        message={
          collectionQuery.error instanceof Error ? collectionQuery.error.message : 'Unknown error'
        }
      />
    );
  }
  if (!routeIdentity && !collection) return <LoadingState label="Loading work…" />;

  return (
    <div className={`ting-work-page${routeIdentity ? ' ting-work-page--detail' : ''}`}>
      <aside className="ting-work-sidebar" aria-label="Work workspace">
        <div className="ting-work-sidebar__brand">
          <span>Work</span>
        </div>
        <nav aria-label="Work views">
          {(Object.keys(FILTER_LABEL) as WorkFilter[]).map((value) => (
            <button
              key={value}
              type="button"
              aria-current={!routeIdentity && filter === value ? 'page' : undefined}
              onClick={() => updateSearch({ filter: value, q: search })}
            >
              <span>{FILTER_LABEL[value]}</span>
            </button>
          ))}
        </nav>
        <section className="ting-work-projects" aria-labelledby="connected-projects-title">
          <h2 id="connected-projects-title">Connected projects</h2>
          {visibleCollection.projects.length > 0 ? (
            visibleCollection.projects.map((project) => (
              <button
                type="button"
                key={project.id}
                aria-current={params.workId === workRouteId(project) ? 'page' : undefined}
                onClick={() => selectWork(project)}
              >
                <GitBranch aria-hidden="true" />
                <span>
                  <strong>{project.title}</strong>
                  <small>{sourceLabel(project) ?? 'Connected source'}</small>
                </span>
              </button>
            ))
          ) : (
            <p>No connected projects</p>
          )}
        </section>
        <div className="ting-work-sidebar__secondary">
          <Link to="/ting/dispatch">
            <CircleDot aria-hidden="true" /> Operations
          </Link>
        </div>
      </aside>

      <div className="ting-work-content" ref={contentRef}>
        {routeIdentity ? (
          detailQuery.isPending ? (
            <LoadingState label="Loading work detail…" />
          ) : detailQuery.isError ? (
            <ErrorState
              title="Work detail could not be loaded"
              message={
                detailQuery.error instanceof Error ? detailQuery.error.message : 'Unknown error'
              }
            />
          ) : (
            <WorkDetailView
              detail={detailQuery.data}
              onBack={() => updateSearch({ filter, q: search })}
              onNew={() => setNewWorkOpen(true)}
              onRefresh={() => detailQuery.refetch()}
            />
          )
        ) : (
          <WorkOverview
            collection={visibleCollection}
            filter={filter}
            search={search}
            onFilter={(next) => updateSearch({ filter: next, q: search })}
            onSearch={(next) => updateSearch({ filter, q: next })}
            onSelect={selectWork}
            onNew={() => setNewWorkOpen(true)}
            onRetry={() => void collectionQuery.refetch()}
            hasNextPage={Boolean(collectionQuery.hasNextPage)}
            loadingMore={collectionQuery.isFetchingNextPage}
            onLoadMore={() => void collectionQuery.fetchNextPage()}
          />
        )}
      </div>
      <NewWorkDialog
        open={newWorkOpen}
        onOpenChange={setNewWorkOpen}
        onTracker={() => setTrackerImportOpen(true)}
      />
      {trackerImportOpen ? (
        <TrackerWorkImportDialog
          open
          onOpenChange={setTrackerImportOpen}
          connectedProjects={visibleCollection.projects}
          onOpenProject={selectWork}
          onImported={(saga) => {
            void pageQueryClient.invalidateQueries({ queryKey: ['ting', 'work'] });
            void navigate({
              to: '/ting/work/$workId' as never,
              params: { workId: `project:${saga.id}` } as never,
            });
          }}
        />
      ) : null}
    </div>
  );
}
