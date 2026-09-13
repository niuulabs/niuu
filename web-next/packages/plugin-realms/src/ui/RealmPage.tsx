import { useState } from 'react';
import { Link, useNavigate, useParams } from '@tanstack/react-router';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { usePluginCtx, useService } from '@niuulabs/plugin-sdk';
import {
  HeroCard,
  MessageRow,
  ResidentLogsView,
  useCreateResidentSession,
  useResidentSessions,
  type ISessionStream,
  type Ravn,
} from '@niuulabs/plugin-ravn';
import {
  WorkflowLaunchModal,
  useWorkflows,
  type IWorkflowService,
  type TrackerIssue,
  type Workflow,
  type WorkflowLaunchRequest,
} from '@niuulabs/plugin-ting';
import {
  ToolBuilderGrantCard,
  autonomyModeCopy,
  decisionStatusCopy,
  reviewKindLabel,
  useDecideReview,
  wakefulnessCopy,
  type DecisionRecord,
  type ReviewItem,
} from '@niuulabs/plugin-valkyrie';
import { LaunchWizard, SectionCard, type VolundrSession } from '@niuulabs/plugin-volundr';
import {
  BudgetBar,
  Chip,
  EmptyState,
  ErrorState,
  LoadingState,
  StateDot,
  Table,
  type DotState,
} from '@niuulabs/ui';
import { useRavens } from '../application/useRealmsHome';
import { useRealmView } from '../application/useRealmView';
import { FIRST_REALM_WALKTHROUGH, useWalkthrough } from '../application/useWalkthrough';
import { ravnForRealm } from '../domain/join';
import { WalkthroughRail } from './WalkthroughRail';

const BUTTON =
  'niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:font-medium niuu:text-text-primary niuu:disabled:opacity-40';
const PRIMARY =
  'niuu:rounded-md niuu:border niuu:border-brand/50 niuu:bg-brand/10 niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:font-medium niuu:text-brand-300 niuu:disabled:opacity-40';

const WAKEFULNESS_DOT: Record<string, DotState> = {
  wakeful: 'healthy',
  watching: 'observing',
  dreaming: 'processing',
  sleeping: 'idle',
};

function IssueTable({ issues, emptyText }: { issues: TrackerIssue[]; emptyText: string }) {
  if (issues.length === 0) return <EmptyState title={emptyText} />;
  return (
    <Table<TrackerIssue>
      columns={[
        { key: 'identifier', header: 'id', render: (row) => <span className="niuu:font-mono niuu:text-xs">{row.identifier}</span>, width: '96px' },
        { key: 'title', header: 'title', render: (row) => <a href={row.url} target="_blank" rel="noreferrer" className="niuu:text-text-primary">{row.title}</a> },
        { key: 'status', header: 'status', render: (row) => <Chip tone="muted">{row.status}</Chip>, width: '120px' },
        { key: 'priority', header: 'p', render: (row) => <span className="niuu:font-mono niuu:text-xs">P{row.priority}</span>, width: '48px' },
      ]}
      rows={issues.slice(0, 12)}
      aria-label="issues"
    />
  );
}

function SessionsTable({ sessions }: { sessions: VolundrSession[] }) {
  if (sessions.length === 0) return <EmptyState title="No sessions yet" description="The resident starts one per ticket it picks up." />;
  return (
    <Table<VolundrSession>
      columns={[
        { key: 'name', header: 'session', render: (row) => <Link to={'/volundr/session/$sessionId' as never} params={{ sessionId: row.id } as never} className="niuu:text-text-primary">{row.name}</Link> },
        { key: 'status', header: 'state', render: (row) => <Chip tone={row.status === 'running' ? 'brand' : 'muted'}>{row.status}</Chip>, width: '140px' },
        { key: 'model', header: 'model', render: (row) => <span className="niuu:font-mono niuu:text-xs">{row.model}</span>, width: '160px' },
      ]}
      rows={sessions.slice(0, 12)}
      aria-label="sessions"
    />
  );
}

function NeedsYou({ items }: { items: ReviewItem[] }) {
  const decide = useDecideReview();
  const pending = items.filter((item) => item.status === 'pending');
  if (pending.length === 0) return <EmptyState title="Nothing waiting on you" />;
  return (
    <div className="niuu:flex niuu:flex-col">
      {pending.map((item) => (
        <div key={item.itemId} className="niuu:flex niuu:items-center niuu:gap-3 niuu:border-b niuu:border-border-subtle niuu:py-2.5" data-testid={`realm-review-${item.itemId}`}>
          <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col">
            <span className="niuu:truncate niuu:text-sm niuu:text-text-primary">{item.title}</span>
            <span className="niuu:truncate niuu:text-xs niuu:text-text-muted">{item.summary}</span>
          </div>
          <Chip tone="muted">{reviewKindLabel(item.kind)}</Chip>
          <button type="button" className={PRIMARY} disabled={decide.isPending} onClick={() => decide.mutate({ itemId: item.itemId, decision: 'approved' })}>
            Approve
          </button>
          <button type="button" className={BUTTON} disabled={decide.isPending} onClick={() => decide.mutate({ itemId: item.itemId, decision: 'rejected' })}>
            Reject
          </button>
        </div>
      ))}
      {decide.error ? <span className="niuu:pt-2 niuu:text-xs niuu:text-critical-fg">{String(decide.error)}</span> : null}
    </div>
  );
}

function WhatItDid({ decisions }: { decisions: DecisionRecord[] }) {
  if (decisions.length === 0) return <EmptyState title="Nothing yet" description="Decisions show here as the resident makes them." />;
  return (
    <div className="niuu:flex niuu:flex-col">
      {decisions.slice(0, 10).map((decision) => (
        <div key={decision.decisionId} className="niuu:flex niuu:gap-3 niuu:border-b niuu:border-border-subtle niuu:py-2">
          <span className="niuu:w-12 niuu:shrink-0 niuu:font-mono niuu:text-[11px] niuu:text-text-faint">{decision.decidedAt.slice(11, 16)}</span>
          <div className="niuu:flex niuu:min-w-0 niuu:flex-col">
            <span className="niuu:text-xs niuu:text-text-primary">{decision.summary || decision.recommendedAction}</span>
            <span className="niuu:text-[11px] niuu:text-text-muted">
              {decisionStatusCopy(decision).label}
              {decision.actionAuthority ? ` · ${decision.actionAuthority}` : ''}
            </span>
          </div>
        </div>
      ))}
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
    <div className="niuu:flex niuu:flex-col niuu:gap-2">
      {latest ? (
        <div className="niuu:flex niuu:max-h-72 niuu:flex-col niuu:gap-1 niuu:overflow-auto">
          {(messages.data ?? []).slice(-12).map((message) => (
            <MessageRow key={message.id} message={message} />
          ))}
        </div>
      ) : (
        <EmptyState title="No conversation yet" description="Start one and ask it anything about the realm." />
      )}
      <div className="niuu:flex niuu:items-center niuu:gap-2">
        <button
          type="button"
          className={PRIMARY}
          disabled={create.isPending}
          onClick={() => create.mutate({ title: `Realm chat ${new Date().toISOString().slice(0, 10)}` })}
        >
          {latest ? 'New conversation' : 'Start a conversation'}
        </button>
        <Link to={'/ravn/ravens' as never} className="niuu:text-xs niuu:text-brand-300">
          Open in Ravn
        </Link>
      </div>
      {create.error ? <span className="niuu:text-xs niuu:text-critical-fg">{String(create.error)}</span> : null}
    </div>
  );
}

export function RealmPage() {
  const { slug } = useParams({ strict: false }) as { slug: string };
  const navigate = useNavigate();
  const data = useRealmView(slug);
  const ravens = useRavens();
  const ravn = ravnForRealm(ravens.data, slug);
  const walkthrough = useWalkthrough(FIRST_REALM_WALKTHROUGH);
  const ctx = usePluginCtx();
  const [launchOpen, setLaunchOpen] = useState(false);
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

  if (data.isLoading) return <LoadingState label="Loading realm…" />;
  if (data.error) return <ErrorState title="Could not load the realm" message={String(data.error)} />;
  if (data.notFound || !data.realm || !data.view) {
    return <ErrorState title="No such realm" message={`There is no realm called ${slug}.`} action={<Link to="/realms" className={BUTTON}>Back to realms</Link>} />;
  }

  const view = data.view;
  const dot: DotState = view.wakefulness ? (WAKEFULNESS_DOT[view.wakefulness] ?? 'unknown') : 'unknown';
  const health = data.environment?.health ?? null;
  const unresolved = data.environment?.unresolvedSignalCount ?? 0;

  return (
    <div className="niuu:flex niuu:h-full" data-testid="realm-page">
      <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col niuu:gap-4 niuu:overflow-auto niuu:p-6">
        <header className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-4">
          <div className="niuu:flex niuu:items-center niuu:gap-3">
            <span className="niuu:flex niuu:h-7 niuu:w-7 niuu:items-center niuu:justify-center niuu:rounded-full niuu:border niuu:border-brand/40 niuu:bg-brand/10 niuu:font-mono niuu:text-xs niuu:font-bold niuu:text-brand">
              {data.realm.name.charAt(0).toUpperCase()}
            </span>
            <div className="niuu:flex niuu:flex-col">
              <span className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-lg niuu:font-semibold niuu:text-text-primary">
                {data.realm.name}
                <Chip tone={view.wakefulness === 'wakeful' ? 'brand' : 'muted'}>
                  <StateDot state={dot} pulse={view.wakefulness === 'wakeful'} size={6} />
                  {view.wakefulness ? wakefulnessCopy(view.wakefulness).label : ravn ? ravn.status : 'no resident'}
                </Chip>
                {view.autonomyMode ? <Chip tone="muted">{autonomyModeCopy(view.autonomyMode as never).label}</Chip> : null}
              </span>
              <span className="niuu:font-mono niuu:text-[11px] niuu:text-text-muted">
                {view.binding?.repo ?? 'no repository'}
                {view.binding?.trackerBoard ? ` · board ${view.binding.trackerBoard}` : ''}
                {view.confidence !== null ? ` · confidence ${view.confidence.toFixed(2)}` : ''}
              </span>
            </div>
          </div>
          <div className="niuu:flex niuu:items-center niuu:gap-2">
            {view.pendingReviews > 0 ? <Chip tone="critical">{view.pendingReviews} need you</Chip> : null}
            <button type="button" className={BUTTON} onClick={() => setLaunchOpen(true)} data-testid="realm-launch-session">
              Launch a session here
            </button>
            <button type="button" className={BUTTON} onClick={() => setWorkflowOpen(true)} data-testid="realm-run-workflow">
              Run a workflow here
            </button>
            <button type="button" className={BUTTON} onClick={() => void navigate({ to: '/realms/new', search: { from: slug } as never })}>
              Clone
            </button>
            <Link to="/realms/$slug/settings" params={{ slug }} className={BUTTON}>
              Settings
            </Link>
          </div>
        </header>

        <div className="niuu:grid niuu:grid-cols-4 niuu:gap-3">
          <SectionCard title={`Intake · ${data.intake.length}`}>
            <IssueTable issues={data.intake} emptyText={view.binding?.trackerBoard ? 'Board is empty' : 'No board bound'} />
          </SectionCard>
          <SectionCard title={`In sessions · ${view.runningSessions}`}>
            <SessionsTable sessions={data.realmSessions} />
          </SectionCard>
          <SectionCard title={`QA findings · ${data.findings.length}`}>
            <IssueTable issues={data.findings} emptyText={view.binding?.bugBoard ? 'No findings' : 'No bug board bound'} />
          </SectionCard>
          <SectionCard title="Health">
            {health ? (
              <div className="niuu:flex niuu:flex-col niuu:gap-2 niuu:text-xs">
                <Chip tone={health === 'healthy' ? 'brand' : 'critical'}>{health}</Chip>
                <span className="niuu:text-text-muted">{unresolved} unresolved signal{unresolved === 1 ? '' : 's'}</span>
              </div>
            ) : (
              <EmptyState title="No environment yet" description="Appears once the resident is online." />
            )}
          </SectionCard>
        </div>

        <div className="niuu:grid niuu:grid-cols-[1.4fr_1fr] niuu:gap-4">
          <div className="niuu:flex niuu:flex-col niuu:gap-4">
            <SectionCard title="Needs you">
              <NeedsYou items={data.realmReviews} />
            </SectionCard>
            <SectionCard title="What it did">
              <WhatItDid decisions={data.decisions} />
            </SectionCard>
            {ravn ? (
              <SectionCard title="Logs">
                <ResidentLogsView ravn={ravn} />
              </SectionCard>
            ) : null}
          </div>
          <div className="niuu:flex niuu:flex-col niuu:gap-4">
            <SectionCard title="Trust">
              <div className="niuu:flex niuu:flex-wrap niuu:gap-1.5 niuu:pb-3">
                {view.grants.map((grant) => (
                  <Chip key={grant.actionClass} tone={grant.level >= 2 ? 'brand' : 'muted'}>
                    {grant.actionClass} · L{grant.level}
                  </Chip>
                ))}
                {view.grants.length === 0 ? <span className="niuu:text-xs niuu:text-text-muted">No grants yet.</span> : null}
              </div>
              <ToolBuilderGrantCard realm={{ slug: data.realm.slug, name: data.realm.name }} />
            </SectionCard>
            <SectionCard title="Budget">
              {data.budget ? (
                <div className="niuu:flex niuu:flex-col niuu:gap-3">
                  <HeroCard spentUsd={data.budget.spentUsd} capUsd={data.budget.capUsd} projectedUsd={data.budget.spentUsd} />
                  <BudgetBar spent={data.budget.spentUsd} cap={data.budget.capUsd} warnAt={Math.round(data.budget.warnAt * 100)} showLabel size="sm" />
                </div>
              ) : (
                <EmptyState title="No budget yet" description="Shows once the resident has spent something." />
              )}
            </SectionCard>
            <SectionCard title="Memory">
              <div className="niuu:flex niuu:flex-col niuu:gap-2 niuu:text-xs">
                <span className="niuu:font-mono niuu:text-text-secondary">{data.mountName ?? 'no mount'}</span>
                <span className="niuu:text-text-muted">{data.mount ? `${data.mount.pages} pages · ${data.mount.status}` : 'not discovered yet'}</span>
                <button
                  type="button"
                  className={`${BUTTON} niuu:self-start`}
                  disabled={!data.mount}
                  onClick={() => {
                    ctx.setTweak('activeMount', data.mountName);
                    void navigate({ to: '/mimir/pages' as never });
                  }}
                >
                  Open realm memory
                </button>
              </div>
            </SectionCard>
            <SectionCard title="Talk to it">
              {ravn ? <TalkToIt ravn={ravn} /> : <EmptyState title="No resident yet" />}
            </SectionCard>
          </div>
        </div>
      </div>

      <WalkthroughRail
        walkthrough={FIRST_REALM_WALKTHROUGH}
        action={
          walkthrough.currentStepId === 'first-answer' ? (
            <button type="button" className={PRIMARY} onClick={() => walkthrough.markDone('first-answer')}>
              I answered its first question
            </button>
          ) : undefined
        }
      />

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
          if (!target) throw new Error('No workflow to run. Create one under Ting › Workflows.');
          await launchWorkflow.mutateAsync({
            id: target.id,
            request: { ...request, repo: request.repo ?? view.binding?.repo, branch: request.branch ?? view.binding?.branch },
          });
          setWorkflowOpen(false);
        }}
      />
    </div>
  );
}
