import { useState } from 'react';
import { Link, useNavigate, useParams, useSearch } from '@tanstack/react-router';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { usePluginCtx, useService } from '@niuulabs/plugin-sdk';
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
import { LaunchWizard, SectionCard } from '@niuulabs/plugin-volundr';
import {
  Chip,
  EmptyState,
  ErrorState,
  LoadingState,
  Modal,
  ProjectIcon,
  SegmentedFilter,
  Table,
} from '@niuulabs/ui';
import { useForgeProjects } from '../application/useProjects';
import { useRealmView } from '../application/useRealmView';
import { FIRST_REALM_WALKTHROUGH } from '../application/useWalkthrough';
import { trustSentence } from '../domain/trust';
import { activeSessionCount } from '../domain/join';
import { ACTION_CLASSES } from '../domain/realm';
import { sessionsWithDescendants } from '../domain/sessionTree';
import { ConnectionsPanel } from './ConnectionsPanel';
import { DeleteWorkspace } from './DeleteWorkspace';
import { SessionsTable } from './SessionsTable';
import { TicketReviewRow } from './TicketReviewRow';
import { TrustPanel } from './TrustPanel';
import { WalkthroughRail } from './WalkthroughRail';

const BUTTON =
  'niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:font-medium niuu:text-text-primary niuu:disabled:opacity-40';
const PRIMARY =
  'niuu:rounded-md niuu:border niuu:border-brand/50 niuu:bg-brand/10 niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:font-medium niuu:text-brand-300 niuu:disabled:opacity-40';
const LINK = 'niuu:text-[11px] niuu:text-brand-300';

const REALM_TABS = ['overview', 'board', 'sessions', 'memory', 'connections', 'settings'] as const;
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
 * Every ticket waiting at a gate on this workspace's board. It is the only part of the
 * overview that asks something of you, so it sits at the top and disappears the moment
 * it is empty.
 */
function NeedsYouStrip({ tickets, workspace }: { tickets: WorkReviewItem[]; workspace: string }) {
  if (tickets.length === 0) return null;
  return (
    <section
      className="niuu:flex niuu:flex-col niuu:rounded-xl niuu:border niuu:border-status-amber/40 niuu:bg-status-amber/5 niuu:px-4 niuu:py-3"
      data-testid="realm-needs-you"
    >
      <span className="niuu:pb-1 niuu:text-sm niuu:font-medium niuu:text-text-primary">
        Needs you · {tickets.length}
      </span>
      {tickets.map((item) => (
        <TicketReviewRow key={item.taskId} item={item} workspace={workspace} />
      ))}
    </section>
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
            description="This tracker is not connected to Work yet. Connect it under Connections to work its tickets on a board."
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
  const projects = useForgeProjects();
  const board = useTrackerBoard(data.view?.binding?.trackerBoard || null);
  const ctx = usePluginCtx();
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
            Back to projects
          </Link>
        }
      />
    );
  }

  const view = data.view;
  const levels = Object.fromEntries(
    ACTION_CLASSES.map((actionClass) => [
      actionClass,
      view.grants.find((grant) => grant.actionClass === actionClass)?.level ?? null,
    ]),
  );
  const pendingCount = data.waitingTickets.length;
  const project = projects.data?.find((entry) => entry.id === data.realm?.project_id) ?? null;
  // Each ticket's coordinator, with everything it launched.
  const workspaceSessions = sessionsWithDescendants(
    data.allSessions,
    (board.detail?.tasks ?? []).flatMap((task) =>
      task.coordinator ? [task.coordinator.sessionId] : [],
    ),
  );

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
                <Chip tone="muted">{view.autonomyProfile}</Chip>
              </span>
              <span className="niuu:font-mono niuu:text-[11px] niuu:text-text-muted">
                {view.binding?.repo || 'no repository'}
                {view.binding?.trackerBoard
                  ? ` · board ${board.saga?.name ?? view.binding.trackerBoard}`
                  : ''}
              </span>
            </div>
          </div>
          <div className="niuu:flex niuu:items-center niuu:gap-2">
            {pendingCount > 0 ? <Chip tone="critical">{pendingCount} need you</Chip> : null}
            <button
              type="button"
              className={BUTTON}
              onClick={() => setLaunchOpen(true)}
              data-testid="realm-launch-session"
            >
              Session
            </button>
            <button
              type="button"
              className={BUTTON}
              onClick={() => setPickerOpen(true)}
              disabled={!workflows.data}
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
            { value: 'sessions', label: 'Sessions', count: workspaceSessions.length },
            { value: 'memory', label: 'Memory' },
            { value: 'connections', label: 'Connections' },
            { value: 'settings', label: 'Settings' },
          ]}
          value={tab}
          onChange={switchTab}
          aria-label="Workspace section"
        />

        {tab === 'overview' ? (
          <>
            <NeedsYouStrip tickets={data.waitingTickets} workspace={view.name} />
            <div className="niuu:grid niuu:grid-cols-3 niuu:gap-3">
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
            </div>

            <SectionCard title="Trust" description={trustSentence(levels)}>
              <div className="niuu:flex niuu:flex-wrap niuu:gap-1" data-testid="trust-summary">
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
              <button
                type="button"
                className={`${LINK} niuu:self-start`}
                onClick={() => switchTab('settings')}
              >
                adjust
              </button>
            </SectionCard>
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

        {tab === 'settings' ? (
          <div className="niuu:flex niuu:flex-col niuu:gap-4" data-testid="realm-settings">
            <TrustPanel slug={slug} view={view} />
            <DeleteWorkspace slug={slug} realmName={data.realm.name} />
          </div>
        ) : null}

        {tab === 'connections' ? (
          <ConnectionsPanel realm={data.realm} view={view} onOpenBoard={() => switchTab('board')} />
        ) : null}

        {tab === 'sessions' ? (
          <div className="niuu:flex niuu:flex-col niuu:gap-4">
            <SectionCard title={`Sessions · ${workspaceSessions.length}`}>
              <SessionsTable sessions={workspaceSessions} />
            </SectionCard>
          </div>
        ) : null}

        {tab === 'memory' ? (
          <div className="niuu:flex niuu:min-h-0 niuu:flex-1 niuu:flex-col niuu:gap-3">
            {data.mountName ? (
              <div className="niuu:flex niuu:items-center niuu:gap-3 niuu:text-xs">
                <span className="niuu:font-mono niuu:text-text-secondary">{data.mountName}</span>
                <span className="niuu:text-text-muted">
                  {data.mount
                    ? `${data.mount.pages} pages · ${data.mount.status}`
                    : 'not discovered yet'}
                </span>
              </div>
            ) : null}
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
                description="This workspace was made without a memory instance of its own. Explore the platform's memory in the Memory scene."
                action={
                  <Link to="/mimir" className={BUTTON}>
                    Open Memory
                  </Link>
                }
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

      <WalkthroughRail walkthrough={FIRST_REALM_WALKTHROUGH} />

      {launchOpen ? (
        <LaunchWizard
          open={launchOpen}
          onOpenChange={setLaunchOpen}
          initialForm={{
            sourcetype: 'git',
            repo: view.binding?.repo ?? '',
            branch: view.binding?.branch ?? '',
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
