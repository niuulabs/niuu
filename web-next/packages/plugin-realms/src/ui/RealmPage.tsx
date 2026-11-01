import { useState, type ReactNode } from 'react';
import { Link, useNavigate, useParams, useSearch } from '@tanstack/react-router';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { usePluginCtx, useService } from '@niuulabs/plugin-sdk';
import { useUiMode } from '@niuulabs/shell';
import {
  MessageRow,
  ResidentLogsView,
  useCreateResidentSession,
  useResidentSessions,
  type ISessionStream,
  type Ravn,
} from '@niuulabs/plugin-ravn';
import {
  TrackerBoard,
  WorkflowLaunchModal,
  useTrackerBoard,
  useWorkflows,
  type IWorkflowService,
  type ReviewItem as WorkReviewItem,
  type TrackerIssue,
  type Workflow,
  type WorkflowLaunchRequest,
} from '@niuulabs/plugin-ting';
import {
  autonomyModeCopy,
  decisionStatusCopy,
  reviewKindLabel,
  useDecideReview,
  wakefulnessCopy,
  type DecisionRecord,
  type ReviewItem,
} from '@niuulabs/plugin-valkyrie';
import { LaunchWizard, SectionCard } from '@niuulabs/plugin-volundr';
import {
  BudgetBar,
  Chip,
  EmptyState,
  ErrorState,
  LoadingState,
  Modal,
  ProjectIcon,
  SegmentedFilter,
  StateDot,
  Table,
  type DotState,
} from '@niuulabs/ui';
import { useForgeProjects } from '../application/useProjects';
import { useRavens } from '../application/useRealmsHome';
import { useRealmView } from '../application/useRealmView';
import { FIRST_REALM_WALKTHROUGH, useWalkthrough } from '../application/useWalkthrough';
import { decisionLine, decisionTone, trustSentence, type ActivityTone } from '../domain/activity';
import { activeSessionCount, ravnForRealm } from '../domain/join';
import { ACTION_CLASSES } from '../domain/realm';
import { sessionsWithDescendants } from '../domain/sessionTree';
import { ConnectionsPanel } from './ConnectionsPanel';
import { ResidentPanel } from './ResidentPanel';
import { SessionsTable } from './SessionsTable';
import { TicketReviewRow } from './TicketReviewRow';
import { WalkthroughRail } from './WalkthroughRail';

const BUTTON =
  'niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:font-medium niuu:text-text-primary niuu:disabled:opacity-40';
const PRIMARY =
  'niuu:rounded-md niuu:border niuu:border-brand/50 niuu:bg-brand/10 niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:font-medium niuu:text-brand-300 niuu:disabled:opacity-40';
const LINK = 'niuu:text-[11px] niuu:text-brand-300';

const WAKEFULNESS_DOT: Record<string, DotState> = {
  wakeful: 'healthy',
  watching: 'observing',
  dreaming: 'processing',
  sleeping: 'unknown',
};

const TONE_DOT: Record<ActivityTone, DotState> = {
  ok: 'healthy',
  warn: 'attention',
  brand: 'observing',
  muted: 'unknown',
};

const REALM_TABS = ['overview', 'board', 'resident', 'sessions', 'memory', 'connections'] as const;
type RealmTab = (typeof REALM_TABS)[number];

// ---------------------------------------------------------------------------
// Overview pieces (artboard E: four compact queues, a timeline, three small cards)
// ---------------------------------------------------------------------------

/** A count you can act on: click to land on the tab that lists the things counted. */
function CountTile({
  id,
  label,
  count,
  hint,
  attention,
  onOpen,
}: {
  id: string;
  label: string;
  count: number;
  hint: string;
  attention?: boolean;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      className="niuu:flex niuu:min-w-0 niuu:flex-col niuu:gap-0.5 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-4 niuu:py-3 niuu:text-left niuu:hover:border-brand/50"
      data-testid={`queue-${id}`}
      onClick={onOpen}
    >
      <span className="niuu:text-[10px] niuu:font-medium niuu:uppercase niuu:tracking-wider niuu:text-text-muted">
        {label}
      </span>
      <span
        className={`niuu:font-mono niuu:text-2xl niuu:font-semibold niuu:leading-tight ${attention && count > 0 ? 'niuu:text-status-amber' : 'niuu:text-text-primary'}`}
      >
        {count}
      </span>
      <span className="niuu:truncate niuu:text-[11px] niuu:text-text-faint">{hint}</span>
    </button>
  );
}

/**
 * Everything the resident is waiting on you for, above everything it did. It is the
 * only part of the overview that asks something of you, so it sits at the top and
 * disappears the moment it is empty.
 */
function NeedsYouStrip({
  reviews,
  tickets,
  workspace,
}: {
  reviews: ReviewItem[];
  tickets: WorkReviewItem[];
  workspace: string;
}) {
  const decide = useDecideReview();
  const [decided, setDecided] = useState<string[]>([]);
  const pending = reviews.filter(
    (item) => item.status === 'pending' && !decided.includes(item.itemId),
  );

  function answer(item: ReviewItem, decision: 'approved' | 'rejected') {
    setDecided((ids) => [...ids, item.itemId]);
    decide.mutate(
      { itemId: item.itemId, decision },
      { onError: () => setDecided((ids) => ids.filter((id) => id !== item.itemId)) },
    );
  }

  if (pending.length + tickets.length === 0) return null;
  return (
    <section
      className="niuu:flex niuu:flex-col niuu:rounded-xl niuu:border niuu:border-status-amber/40 niuu:bg-status-amber/5 niuu:px-4 niuu:py-3"
      data-testid="realm-needs-you"
    >
      <span className="niuu:pb-1 niuu:text-sm niuu:font-medium niuu:text-text-primary">
        Needs you · {pending.length + tickets.length}
      </span>
      {tickets.map((item) => (
        <TicketReviewRow key={item.taskId} item={item} workspace={workspace} />
      ))}
      {pending.map((item) => (
        <div
          key={item.itemId}
          className="niuu:flex niuu:items-center niuu:gap-3 niuu:border-b niuu:border-border-subtle niuu:py-2 niuu:last:border-b-0"
          data-testid={`realm-review-${item.itemId}`}
        >
          <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col">
            <span className="niuu:truncate niuu:text-xs niuu:text-text-primary">{item.title}</span>
            <span className="niuu:truncate niuu:text-[11px] niuu:text-text-muted">
              {item.summary}
            </span>
          </div>
          <Chip tone="muted">{reviewKindLabel(item.kind)}</Chip>
          <button
            type="button"
            className={PRIMARY}
            disabled={decide.isPending}
            onClick={() => answer(item, 'approved')}
          >
            Approve
          </button>
          <button
            type="button"
            className={BUTTON}
            disabled={decide.isPending}
            onClick={() => answer(item, 'rejected')}
          >
            Reject
          </button>
          <Link to={'/valkyrie/inbox' as never} className={LINK}>
            Open
          </Link>
        </div>
      ))}
      {decide.error ? (
        <span className="niuu:pt-2 niuu:text-xs niuu:text-critical-fg">{String(decide.error)}</span>
      ) : null}
    </section>
  );
}

function TimelineRow({
  time,
  tone,
  text,
  tag,
}: {
  time: string;
  tone: ActivityTone;
  text: string;
  tag: string;
}) {
  return (
    <div className="niuu:flex niuu:items-start niuu:gap-2.5 niuu:border-b niuu:border-border-subtle niuu:py-2">
      <span className="niuu:w-10 niuu:shrink-0 niuu:pt-0.5 niuu:font-mono niuu:text-[11px] niuu:text-text-faint">
        {time}
      </span>
      <span className="niuu:pt-1.5">
        <StateDot state={TONE_DOT[tone]} size={6} />
      </span>
      <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col">
        <span className="niuu:text-xs niuu:text-text-primary">{text}</span>
        <span className="niuu:text-[11px] niuu:text-text-muted">{tag}</span>
      </div>
    </div>
  );
}

/** What the resident did, latest first. What it is asking sits above, in the strip. */
function Timeline({ decisions }: { decisions: DecisionRecord[] }) {
  if (decisions.length === 0) {
    return (
      <span className="niuu:py-3 niuu:text-xs niuu:text-text-faint">
        Nothing yet. Decisions show here as the resident makes them.
      </span>
    );
  }
  return (
    <div className="niuu:flex niuu:flex-col">
      {decisions.slice(0, 8).map((decision) => (
        <TimelineRow
          key={decision.decisionId}
          time={decision.decidedAt.slice(11, 16)}
          tone={decisionTone(decision)}
          text={decisionLine(decision)}
          tag={`${decisionStatusCopy(decision).label}${decision.actionAuthority ? ` · ${decision.actionAuthority}` : ''}`}
        />
      ))}
    </div>
  );
}

function SmallCard({
  title,
  aside,
  children,
  grow,
}: {
  title: string;
  aside?: ReactNode;
  children: ReactNode;
  grow?: boolean;
}) {
  return (
    <div
      className={`niuu:flex niuu:flex-col niuu:gap-2 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-3.5 ${grow ? 'niuu:min-h-0 niuu:flex-1' : ''}`}
    >
      <div className="niuu:flex niuu:items-center niuu:justify-between">
        <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">{title}</span>
        {aside}
      </div>
      {children}
    </div>
  );
}

function TalkToIt({ ravn }: { ravn: Ravn }) {
  const sessions = useResidentSessions(ravn, true);
  const create = useCreateResidentSession(ravn);
  const stream = useService<ISessionStream>('ravn.sessions');
  const latest = sessions.data?.[0] ?? null;
  const messages = useQuery({
    queryKey: ['ravn', 'messages', latest?.id],
    queryFn: () => stream.getMessages(latest!.id, ravn.instanceId, ravn.id),
    enabled: latest !== null,
    refetchInterval: 5_000,
  });
  return (
    <div className="niuu:flex niuu:min-h-0 niuu:flex-1 niuu:flex-col niuu:gap-2">
      {latest ? (
        <div className="niuu:flex niuu:max-h-64 niuu:min-h-0 niuu:flex-1 niuu:flex-col niuu:gap-1 niuu:overflow-auto">
          {(messages.data ?? []).slice(-12).map((message) => (
            <MessageRow key={message.id} message={message} />
          ))}
        </div>
      ) : (
        <span className="niuu:text-xs niuu:text-text-faint">
          No conversation yet. Ask it anything about the workspace.
        </span>
      )}
      <div className="niuu:flex niuu:items-center niuu:gap-2">
        <button
          type="button"
          className={PRIMARY}
          disabled={create.isPending}
          onClick={() =>
            create.mutate({ title: `Workspace chat ${new Date().toISOString().slice(0, 10)}` })
          }
        >
          {latest ? 'New conversation' : 'Start a conversation'}
        </button>
        <Link to={'/ravn/ravens' as never} className={LINK}>
          Open in Agents
        </Link>
      </div>
      {create.error ? (
        <span className="niuu:text-xs niuu:text-critical-fg">{String(create.error)}</span>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Board tab
// ---------------------------------------------------------------------------

function IssueTable({ issues, emptyText }: { issues: TrackerIssue[]; emptyText: string }) {
  if (issues.length === 0) return <EmptyState title={emptyText} />;
  return (
    <Table<TrackerIssue>
      columns={[
        {
          key: 'identifier',
          header: 'id',
          render: (row) => <span className="niuu:font-mono niuu:text-xs">{row.identifier}</span>,
          width: '96px',
        },
        {
          key: 'title',
          header: 'title',
          render: (row) => (
            <a href={row.url} target="_blank" rel="noreferrer" className="niuu:text-text-primary">
              {row.title}
            </a>
          ),
        },
        {
          key: 'status',
          header: 'status',
          render: (row) => <Chip tone="muted">{row.status}</Chip>,
          width: '120px',
        },
        {
          key: 'priority',
          header: 'p',
          render: (row) => <span className="niuu:font-mono niuu:text-xs">P{row.priority}</span>,
          width: '48px',
        },
      ]}
      rows={issues}
      aria-label="issues"
    />
  );
}

/** The workspace's board, or its tickets until the tracker has a workflow to draw columns. */
function BoardTab({
  trackerBoard,
  bugBoard,
  intake,
  findings,
  error,
  onConnect,
}: {
  trackerBoard: string;
  bugBoard: string;
  intake: TrackerIssue[];
  findings: TrackerIssue[];
  error: unknown;
  onConnect: () => void;
}) {
  if (!trackerBoard) {
    return (
      <EmptyState
        title="No tracker connected"
        description="A workspace works without one. Connect a tracker to give it a board."
        action={
          <button type="button" className={PRIMARY} onClick={onConnect}>
            Connect a tracker
          </button>
        }
      />
    );
  }
  return (
    <div className="niuu:flex niuu:min-h-0 niuu:flex-1 niuu:flex-col niuu:gap-4">
      {error ? <ErrorState title="Could not load the tickets" message={String(error)} /> : null}
      <TrackerBoard
        trackerId={trackerBoard}
        fallback={
          <SectionCard
            title={`Tickets · ${intake.length}`}
            description="Choose board workflows under Connections to run them from here."
          >
            <IssueTable issues={intake} emptyText="Board is empty" />
          </SectionCard>
        }
      />
      {bugBoard ? (
        <SectionCard title={`QA findings · ${findings.length}`}>
          <IssueTable issues={findings} emptyText="No findings" />
        </SectionCard>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function RealmPage() {
  const { slug } = useParams({ strict: false }) as { slug: string };
  const navigate = useNavigate();
  const data = useRealmView(slug);
  const ravens = useRavens();
  const ravn = ravnForRealm(ravens.data, slug, data.realm?.id);
  const projects = useForgeProjects();
  const board = useTrackerBoard(data.view?.binding?.trackerBoard || null);
  const walkthrough = useWalkthrough(FIRST_REALM_WALKTHROUGH);
  const ctx = usePluginCtx();
  const mode = useUiMode();
  const search = useSearch({ strict: false }) as { tab?: string };
  const [tab, setTab] = useState<RealmTab>(
    REALM_TABS.find((entry) => entry === search.tab) ?? 'overview',
  );
  const [launchOpen, setLaunchOpen] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [workflowOpen, setWorkflowOpen] = useState(false);
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const workflows = useWorkflows();
  const workflowService = useService<IWorkflowService>('ting.workflows');
  const queryClient = useQueryClient();
  const launchWorkflow = useMutation({
    mutationFn: ({ id, request }: { id: string; request: WorkflowLaunchRequest }) =>
      workflowService.launchWorkflow(id, request),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['volundr', 'sessions'] }),
  });

  if (data.isLoading) return <LoadingState label="Loading workspace…" />;
  if (data.error)
    return <ErrorState title="Could not load the workspace" message={String(data.error)} />;
  if (data.notFound || !data.realm || !data.view) {
    return (
      <ErrorState
        title="No such workspace"
        message={`There is no workspace called ${slug}.`}
        action={
          <Link to="/realms" className={BUTTON}>
            Back to workspaces
          </Link>
        }
      />
    );
  }

  const view = data.view;
  const dot: DotState = view.wakefulness
    ? (WAKEFULNESS_DOT[view.wakefulness] ?? 'unknown')
    : 'unknown';
  const health = data.environment?.health ?? null;
  const unresolved = data.environment?.unresolvedSignalCount ?? 0;
  const levels = Object.fromEntries(
    ACTION_CLASSES.map((actionClass) => [
      actionClass,
      view.grants.find((grant) => grant.actionClass === actionClass)?.level ?? null,
    ]),
  );
  const pendingCount =
    data.realmReviews.filter((item) => item.status === 'pending').length +
    data.waitingTickets.length;
  const project = projects.data?.find((entry) => entry.id === data.realm?.project_id) ?? null;
  // Its own sessions and each ticket's coordinator, with everything they launched.
  const workspaceSessions = sessionsWithDescendants(data.allSessions, [
    ...data.realmSessions.map((session) => session.id),
    ...(board.detail?.tasks ?? []).flatMap((task) =>
      task.coordinator ? [task.coordinator.sessionId] : [],
    ),
  ]);

  // Simple mode has whole pages for these; Advanced keeps the dialogs it knows.
  const startSession = () => {
    if (mode === 'advanced') {
      setLaunchOpen(true);
      return;
    }
    void navigate({
      to: '/volundr/sessions/new' as never,
      search: {
        repo: view.binding?.repo ?? '',
        branch: view.binding?.branch ?? '',
        persona: view.personaName,
      } as never,
    });
  };

  const runWorkflow = () => {
    if (mode === 'advanced') {
      setPickerOpen(true);
      return;
    }
    void navigate({
      to: '/ting/workflows' as never,
      search: { repo: view.binding?.repo ?? '' } as never,
    });
  };

  const switchTab = (next: RealmTab) => {
    if (next === 'memory' && data.mountName) ctx.setTweak('activeMount', data.mountName);
    setTab(next);
  };

  return (
    <div className="niuu:flex niuu:h-full" data-testid="realm-page">
      <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col niuu:gap-4 niuu:overflow-auto niuu:px-8 niuu:py-6">
        <header className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-4">
          <div className="niuu:flex niuu:items-center niuu:gap-3">
            <span className="niuu:flex niuu:h-7 niuu:w-7 niuu:items-center niuu:justify-center niuu:rounded-full niuu:border niuu:border-brand/40 niuu:bg-brand/10 niuu:font-mono niuu:text-xs niuu:font-bold niuu:text-brand">
              {data.realm.name.charAt(0).toUpperCase()}
            </span>
            <div className="niuu:flex niuu:flex-col">
              {data.realm.project_id ? (
                <Link
                  to={'/realms/projects/$projectId' as never}
                  params={{ projectId: data.realm.project_id } as never}
                  className={`${LINK} niuu:inline-flex niuu:items-center niuu:gap-1`}
                  data-testid="realm-project-link"
                >
                  <ProjectIcon size={12} />
                  {project?.name ?? 'Project'}
                </Link>
              ) : null}
              <span className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-[17px] niuu:font-semibold niuu:text-text-primary">
                {data.realm.name}
                <Chip tone={view.wakefulness === 'wakeful' ? 'brand' : 'muted'}>
                  <StateDot state={dot} pulse={view.wakefulness === 'wakeful'} size={6} />
                  {view.wakefulness
                    ? wakefulnessCopy(view.wakefulness).label
                    : ravn
                      ? ravn.status
                      : 'no resident'}
                </Chip>
                {view.autonomyMode ? (
                  <Chip tone="muted">{autonomyModeCopy(view.autonomyMode as never).label}</Chip>
                ) : null}
              </span>
              <span className="niuu:font-mono niuu:text-[11px] niuu:text-text-muted">
                {view.binding?.repo || 'no repository'}
                {view.binding?.trackerBoard
                  ? ` · board ${board.saga?.name ?? view.binding.trackerBoard}`
                  : ''}
                {view.confidence !== null ? ` · confidence ${view.confidence.toFixed(2)}` : ''}
              </span>
            </div>
          </div>
          <div className="niuu:flex niuu:items-center niuu:gap-2">
            {pendingCount > 0 ? <Chip tone="critical">{pendingCount} need you</Chip> : null}
            <button
              type="button"
              className={BUTTON}
              onClick={startSession}
              data-testid="realm-launch-session"
            >
              Session
            </button>
            <button
              type="button"
              className={BUTTON}
              onClick={runWorkflow}
              disabled={mode === 'advanced' && !workflows.data}
              title="Run a workflow in this workspace"
              data-testid="realm-run-workflow"
            >
              Workflow
            </button>
            <button
              type="button"
              className={BUTTON}
              onClick={() => void navigate({ to: '/realms/new', search: { from: slug } as never })}
            >
              Clone
            </button>
          </div>
        </header>

        <SegmentedFilter<RealmTab>
          options={[
            { value: 'overview', label: 'Overview' },
            { value: 'board', label: 'Board', count: data.intake.length + data.findings.length },
            { value: 'resident', label: 'Resident' },
            { value: 'sessions', label: 'Sessions', count: workspaceSessions.length },
            { value: 'memory', label: 'Memory' },
            { value: 'connections', label: 'Connections' },
          ]}
          value={tab}
          onChange={switchTab}
          aria-label="Workspace section"
        />

        {tab === 'overview' ? (
          <>
            <NeedsYouStrip
              reviews={data.realmReviews}
              tickets={data.waitingTickets}
              workspace={view.name}
            />
            <div className="niuu:grid niuu:grid-cols-4 niuu:gap-3">
              <CountTile
                id="intake"
                label="Intake"
                count={data.intake.length}
                hint={view.binding?.trackerBoard ? 'tickets on the board' : 'no tracker'}
                onOpen={() => switchTab('board')}
              />
              <CountTile
                id="sessions"
                label="In sessions"
                count={activeSessionCount(workspaceSessions)}
                hint={`${workspaceSessions.length} in all`}
                onOpen={() => switchTab('sessions')}
              />
              <CountTile
                id="findings"
                label="QA findings"
                count={data.findings.length}
                hint={view.binding?.bugBoard ? 'on the bug board' : 'no bug board'}
                onOpen={() => switchTab('board')}
              />
              <CountTile
                id="health"
                label="Health signals"
                count={unresolved}
                hint={
                  data.environment
                    ? `${health} · ${data.environment.signalCount} seen`
                    : 'no environment yet'
                }
                attention
                onOpen={() => switchTab('resident')}
              />
            </div>

            <div className="niuu:grid niuu:min-h-0 niuu:flex-1 niuu:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)] niuu:gap-4">
              <div className="niuu:flex niuu:min-h-0 niuu:flex-col niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-4 niuu:pb-1 niuu:pt-3">
                <div className="niuu:flex niuu:items-center niuu:justify-between niuu:pb-1">
                  <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">
                    What it did
                  </span>
                  <span className="niuu:text-xs niuu:text-text-muted">
                    latest ·{' '}
                    <Link to={'/valkyrie' as never} className={LINK}>
                      full log
                    </Link>
                  </span>
                </div>
                <div className="niuu:min-h-0 niuu:overflow-auto">
                  <Timeline decisions={data.decisions} />
                </div>
              </div>
              <div className="niuu:flex niuu:min-h-0 niuu:flex-col niuu:gap-3">
                <SmallCard
                  title="Trust"
                  aside={
                    <button type="button" className={LINK} onClick={() => switchTab('resident')}>
                      adjust
                    </button>
                  }
                >
                  <div className="niuu:flex niuu:flex-wrap niuu:gap-1">
                    {ACTION_CLASSES.map((actionClass) => {
                      const level = levels[actionClass] ?? null;
                      return (
                        <Chip
                          key={actionClass}
                          tone={level === null ? 'muted' : level >= 2 ? 'brand' : 'default'}
                        >
                          {actionClass}
                          {level !== null ? ` · L${level}` : ''}
                        </Chip>
                      );
                    })}
                  </div>
                  <span className="niuu:text-[11px] niuu:text-text-muted">
                    {trustSentence(levels)}
                  </span>
                </SmallCard>
                <SmallCard
                  title="Budget"
                  aside={
                    data.budget ? (
                      <span className="niuu:font-mono niuu:text-xs niuu:text-text-secondary">
                        ${data.budget.spentUsd.toFixed(0)} / ${data.budget.capUsd.toFixed(0)}
                      </span>
                    ) : null
                  }
                >
                  {data.budget ? (
                    <BudgetBar
                      spent={data.budget.spentUsd}
                      cap={data.budget.capUsd}
                      warnAt={Math.round(data.budget.warnAt * 100)}
                      size="sm"
                    />
                  ) : (
                    <span className="niuu:text-[11px] niuu:text-text-faint">
                      Nothing spent yet.
                    </span>
                  )}
                </SmallCard>
                <SmallCard title="Talk to it" grow>
                  {ravn ? (
                    <TalkToIt ravn={ravn} />
                  ) : (
                    <span className="niuu:text-xs niuu:text-text-faint">No resident yet.</span>
                  )}
                </SmallCard>
              </div>
            </div>
          </>
        ) : null}

        {tab === 'board' ? (
          <BoardTab
            trackerBoard={view.binding?.trackerBoard ?? ''}
            bugBoard={view.binding?.bugBoard ?? ''}
            intake={data.intake}
            findings={data.findings}
            error={data.ticketsError}
            onConnect={() => switchTab('connections')}
          />
        ) : null}

        {tab === 'resident' ? (
          <ResidentPanel
            slug={slug}
            realmName={data.realm.name}
            realmDisplay={{ slug: data.realm.slug, name: data.realm.name }}
            view={view}
            ravn={ravn}
            health={
              <SectionCard title="Health">
                {data.environment ? (
                  <div className="niuu:flex niuu:flex-col niuu:gap-2 niuu:text-xs">
                    <Chip tone={health === 'healthy' ? 'brand' : 'critical'}>{health}</Chip>
                    <span className="niuu:text-text-muted">
                      {unresolved} unresolved of {data.environment.signalCount} signals · last{' '}
                      {data.environment.lastSignalAt.slice(0, 16).replace('T', ' ')}
                    </span>
                  </div>
                ) : (
                  <EmptyState
                    title="No environment yet"
                    description="Appears once the resident is online."
                  />
                )}
              </SectionCard>
            }
          />
        ) : null}

        {tab === 'connections' ? <ConnectionsPanel realm={data.realm} view={view} /> : null}

        {tab === 'sessions' ? (
          <div className="niuu:flex niuu:flex-col niuu:gap-4">
            <SectionCard title={`Sessions · ${workspaceSessions.length}`}>
              <SessionsTable sessions={workspaceSessions} />
            </SectionCard>
            {ravn ? (
              <SectionCard title="Resident logs">
                <ResidentLogsView ravn={ravn} />
              </SectionCard>
            ) : null}
          </div>
        ) : null}

        {tab === 'memory' ? (
          <div className="niuu:flex niuu:min-h-0 niuu:flex-1 niuu:flex-col niuu:gap-3">
            <div className="niuu:flex niuu:items-center niuu:gap-3 niuu:text-xs">
              <span className="niuu:font-mono niuu:text-text-secondary">
                {data.mountName ?? 'no mount'}
              </span>
              <span className="niuu:text-text-muted">
                {data.mount
                  ? `${data.mount.pages} pages · ${data.mount.status}`
                  : 'not discovered yet'}
              </span>
            </div>
            {data.mount && data.mountName ? (
              <EmptyState
                title="Workspace memory"
                description="Explore this workspace's pages, links and proof in the Memory scene."
                action={
                  <Link to="/mimir" search={{ mount: data.mountName }} className={BUTTON}>
                    Explore this workspace&apos;s memory
                  </Link>
                }
              />
            ) : data.mountName ? (
              <EmptyState
                title="Workspace memory is still starting"
                description="The memory instance created with the workspace has not been discovered yet."
              />
            ) : (
              <EmptyState
                title="No workspace memory"
                description="This resident keeps its own memory. A workspace made around a running resident does not create another."
              />
            )}
            {data.mountName ? (
              <span className="niuu:text-xs niuu:text-text-muted">
                Writes under <code>realms/{slug}/</code> are routed to this mount; the charter lives
                at <code>realms/{slug}/charter.md</code>.
              </span>
            ) : null}
          </div>
        ) : null}
      </div>

      <WalkthroughRail
        walkthrough={FIRST_REALM_WALKTHROUGH}
        action={
          walkthrough.currentStepId === 'first-answer' ? (
            <button
              type="button"
              className={PRIMARY}
              onClick={() => walkthrough.markDone('first-answer')}
            >
              I answered its first question
            </button>
          ) : undefined
        }
      />

      {launchOpen ? (
        <LaunchWizard
          open={launchOpen}
          onOpenChange={setLaunchOpen}
          initialForm={{
            sourcetype: 'git',
            repo: view.binding?.repo ?? '',
            branch: view.binding?.branch ?? '',
            personaName: view.personaName,
          }}
        />
      ) : null}
      <Modal
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        title="Run a workflow here"
        description={`The workflow starts against ${view.binding?.repo ?? 'this workspace'}${view.binding?.branch ? ` on ${view.binding.branch}` : ''}.`}
      >
        <div className="niuu:flex niuu:flex-col niuu:gap-2" data-testid="workflow-picker">
          {(workflows.data ?? []).length === 0 ? (
            <EmptyState
              title="No workflows yet"
              description="Build one under Work › Workflows, then run it here."
              action={
                <Link to={'/ting/workflows' as never} className={PRIMARY}>
                  Open Work › Workflows
                </Link>
              }
            />
          ) : (
            (workflows.data ?? []).map((entry) => (
              <button
                key={entry.id}
                type="button"
                className={`${BUTTON} niuu:text-left`}
                onClick={() => {
                  setWorkflow(entry);
                  setPickerOpen(false);
                  setWorkflowOpen(true);
                }}
              >
                {entry.name}
                {entry.description ? (
                  <span className="niuu:ml-2 niuu:text-[11px] niuu:text-text-muted">
                    {entry.description}
                  </span>
                ) : null}
              </button>
            ))
          )}
          <Link to={'/ting/workflows' as never} className={`${LINK} niuu:self-start`}>
            Manage workflows
          </Link>
        </div>
      </Modal>
      <WorkflowLaunchModal
        open={workflowOpen}
        onOpenChange={(open) => {
          setWorkflowOpen(open);
          if (!open) setWorkflow(null);
        }}
        workflow={workflow ?? workflows.data?.[0] ?? null}
        launching={launchWorkflow.isPending}
        onLaunch={async (request) => {
          const target = workflow ?? workflows.data?.[0];
          if (!target) throw new Error('No workflow to run. Create one under Work › Workflows.');
          await launchWorkflow.mutateAsync({
            id: target.id,
            request: {
              ...request,
              repo: request.repo ?? view.binding?.repo,
              branch: request.branch ?? view.binding?.branch,
            },
          });
          setWorkflowOpen(false);
        }}
      />
    </div>
  );
}
