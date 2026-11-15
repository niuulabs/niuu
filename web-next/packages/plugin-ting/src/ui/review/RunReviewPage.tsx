import './TicketReviewPage.css';
import { useMemo } from 'react';
import { Link, useNavigate, useParams, useSearch } from '@tanstack/react-router';
import { ArrowLeft, ExternalLink } from 'lucide-react';
import { MarkdownContent } from '@niuulabs/ui';
import { isPending, type RunReview, type RunReviews } from '../../domain/review';
import { boardSteps, gateSteps } from '../../domain/runSteps';
import { CONDITION_LABEL } from '../../domain/workBoard';
import type { WorkBoardLayout, WorkTask } from '../../domain/work';
import {
  artifactKeysShown,
  artifactReview,
  artifactsFromPublications,
  gatesWithArtifacts,
  revisionPrompt,
  sessionArtifacts,
  versionDocument,
  type Artifact,
  type Publication,
  type RunRef,
  type SessionRef,
  RUN_ENDED,
} from '../../domain/artifacts';
import { CoordinatorChat } from '../CoordinatorChat';
import { useRunGateDecision, useRunReviews, useWorkDetail } from '../useWork';
import {
  useArtifactSession,
  useArtifactTexts,
  useRun,
  useRunPublications,
  useSendToRun,
  useSessionPublications,
  useStartRevision,
  useWaiting,
} from '../useArtifacts';
import { ReviewWorkspace, RunSteps, type ReviewSend } from './ReviewWorkspace';
import { reviewBack, useReturnTo, type ReviewBack } from './reviewBack';

/** A review opened from nowhere in particular goes back to the list of everything reviewed. */
const ARTIFACTS_HOME: ReviewBack = {
  pathname: '/ting/artifacts',
  search: {},
  label: 'Back to Artifacts',
};

/** A run has no board to ask, so its waiting gate is the one its log shows as pending. */
function pendingGate(reviews: readonly RunReview[]) {
  const revision = reviews.flatMap((review) => review.revisions).find(isPending);
  return revision ? { id: revision.gateId, activationId: revision.activationId } : null;
}

/** A member's session is named by its role in the run: `reviewer` reads as Reviewer. */
function memberLabel(member: string): string {
  return member.charAt(0).toUpperCase() + member.slice(1);
}

function runEnded(run: RunRef): boolean {
  return RUN_ENDED.has(run.condition);
}

function noteForRun(artifact: Artifact, notes: string): string {
  const latest = artifact.versions[artifact.versions.length - 1];
  return `Notes on the ${artifact.title.toLowerCase()} (${latest?.label ?? 'latest version'}, artifact key ${artifact.key}):\n\n${notes}`;
}

function textFailures(failed: ReturnType<typeof useArtifactTexts>['failed']) {
  if (failed.length === 0) return null;
  return (
    <p className="ting-review__error" role="alert">
      {failed
        .map(
          ({ version, reason }) =>
            `${version.name} (${version.label}) could not be read: ${reason}`,
        )
        .join(' ')}
    </p>
  );
}

/** A Forge session the run worked in, opened on its live page with a way back here. */
function SessionLink({
  sessionId,
  instanceId,
  label,
  state,
}: {
  sessionId: string;
  instanceId: string | null;
  label: string;
  state: string;
}) {
  const returnTo = useReturnTo();
  return (
    <li>
      <Link
        to={'/volundr/sessions/$sessionId' as never}
        params={{ sessionId } as never}
        search={{ ...(instanceId ? { instance_id: instanceId } : {}), returnTo } as never}
      >
        {label}
      </Link>
      <span>{state}</span>
    </li>
  );
}

function RunWorkspace({
  record,
  run,
  ticket,
  board,
  publications,
  initialReview,
  back,
}: {
  record: RunReviews;
  run: RunRef;
  /** The ticket the run was started for: the page is that ticket's review, with its coordinator. */
  ticket: WorkTask | null;
  /** The ticket's board, which names the columns the run moves through. */
  board: WorkBoardLayout | null;
  publications: readonly Publication[];
  initialReview: string | null;
  back: ReviewBack;
}) {
  const navigate = useNavigate();
  const waiting = useWaiting();
  const decide = useRunGateDecision(record.run.slug);
  const message = useSendToRun();
  const revise = useStartRevision();
  const gate = pendingGate(record.reviews);
  const brief = record.run.prompt;

  const presented = useMemo(
    () => artifactsFromPublications(run, publications, waiting),
    [run, publications, waiting],
  );
  const read = useArtifactTexts(presented);
  // A gate that shows an artifact reads it in its own panel; the rest get a tab each.
  const shownByGates = artifactKeysShown(record.reviews, presented);
  const artifacts = presented.filter((artifact) => !shownByGates.has(artifact.key));
  const reviews = [
    ...gatesWithArtifacts(record.reviews, presented, read.texts),
    ...artifacts.map((artifact) => artifactReview(artifact, record.reviews, read.texts)),
  ];

  function send(review: RunReview): ReviewSend | null {
    const artifact = artifacts.find((item) => item.id === review.columnId);
    if (!artifact) return null;
    if (!runEnded(run)) {
      return {
        title: 'Send back to the run',
        hint: 'The run is still working. Your notes arrive in the chat of the session that presented this, as your next message.',
        label: 'Send to the run',
        busy: message.isPending,
        failure: message.error,
        onSend: (notes, onSuccess) =>
          message.mutate(
            { session: artifact.session, content: noteForRun(artifact, notes) },
            { onSuccess },
          ),
      };
    }
    const latest = artifact.versions[artifact.versions.length - 1];
    if (!run.workflowId || !latest) return null;
    const document = versionDocument(latest, read.texts.get(latest.revisionId));
    return {
      title: 'Start a revision',
      hint: `This run has ended. A new ${record.run.workflowName} run starts from its brief, this version and your notes.`,
      label: 'Start a revision',
      busy: revise.isPending,
      failure: revise.error,
      onSend: (notes, onSuccess) =>
        revise.mutate(
          {
            workflowId: run.workflowId!,
            prompt: revisionPrompt(artifact, latest, document, brief, notes),
            repo: record.run.repo || undefined,
            branch: record.run.branch || undefined,
            connectionId: run.instanceId ?? undefined,
            workflowVersion: run.workflowVersion ?? undefined,
          },
          {
            onSuccess: (started) => {
              onSuccess();
              void navigate({ to: `/ting/artifacts/${started.slug}` as never });
            },
          },
        ),
    };
  }

  const unread = textFailures(read.failed);
  const steps =
    ticket && board && run.workflowId
      ? boardSteps(board, run.workflowId, ticket.board)
      : gateSteps(record.reviews);

  const rail = (
    <>
      {ticket ? (
        <a href={ticket.source.url} target="_blank" rel="noreferrer">
          Open {ticket.source.identifier} <ExternalLink aria-hidden="true" />
        </a>
      ) : null}
      {ticket?.description ? (
        <div className="ting-review__markdown">
          <MarkdownContent content={ticket.description} />
        </div>
      ) : null}
      {brief ? (
        <details className="ting-review__brief">
          <summary>What it was asked to do</summary>
          <div className="ting-review__markdown">
            <MarkdownContent content={brief} />
          </div>
        </details>
      ) : null}
      <h2>Sessions</h2>
      <ul className="ting-review__sessions">
        {ticket?.coordinator ? (
          <SessionLink
            sessionId={ticket.coordinator.sessionId}
            instanceId={ticket.coordinator.connectionId}
            label="Coordinator"
            state={ticket.coordinator.status === 'active' ? 'Active' : 'Closed'}
          />
        ) : null}
        {run.sessions.map((session) => (
          <SessionLink
            key={session.sessionId}
            sessionId={session.sessionId}
            instanceId={session.instanceId}
            label={session.member ? memberLabel(session.member) : `${record.run.workflowName} run`}
            state={CONDITION_LABEL[run.condition]}
          />
        ))}
      </ul>
      {record.run.branch ? <span>On {record.run.branch}</span> : null}
      {record.run.startedBy ? <span>Started by {record.run.startedBy}</span> : null}
    </>
  );

  if (reviews.length === 0) {
    return (
      <div className="ting-review">
        <header className="ting-review__head">
          <Link
            to={back.pathname as never}
            search={back.search as never}
            className="ting-review__back"
          >
            <ArrowLeft aria-hidden="true" /> {back.label}
          </Link>
          <div className="ting-review__title">
            <div>
              <span className="ting-work-task__key">{ticket?.source.identifier ?? 'Run'}</span>
              <h1 title={ticket?.title ?? record.run.title}>{ticket?.title ?? record.run.title}</h1>
            </div>
            <span className="ting-review__workflow">{record.run.workflowName}</span>
          </div>
          <span className="ting-review__status">{CONDITION_LABEL[run.condition]}</span>
          {steps.length > 0 ? (
            <RunSteps workflowName={record.run.workflowName} steps={steps} />
          ) : null}
        </header>
        <div className="ting-review__columns ting-review__columns--wide">
          <aside className="ting-review__ticket" aria-label={ticket ? 'Ticket' : 'Run'}>
            <h2>{ticket ? 'Ticket' : 'Run'}</h2>
            {rail}
          </aside>
          <main className="ting-review__subject">
            <p className="ting-review__state">
              {record.run.workflowName} has put nothing in front of a person yet. Its sessions show
              what it is doing.
            </p>
          </main>
        </div>
      </div>
    );
  }
  return (
    <>
      {unread}
      <ReviewWorkspace
        subject={{
          identifier: ticket?.source.identifier ?? null,
          // What the run is about; the workflow is named on the path beside it.
          title: ticket?.title ?? record.run.title,
          gate,
          pullRequestUrl: ticket?.workflowRun?.prUrl ?? null,
          canAsk:
            ticket?.coordinator?.status === 'active' && Boolean(ticket.coordinator.chatEndpoint),
        }}
        board={board}
        reviews={reviews}
        steps={steps}
        initialReview={initialReview}
        back={back}
        railLabel={ticket ? 'Ticket' : 'Run'}
        talk={
          ticket
            ? (talk) => (
                <CoordinatorChat
                  coordinator={ticket.coordinator}
                  name={ticket.source.identifier}
                  {...talk}
                />
              )
            : undefined
        }
        rail={rail}
        deciding={decide.isPending}
        decisionFailure={decide.error}
        onDecide={(decision, onSuccess) => decide.mutate(decision, { onSuccess })}
        send={send}
      />
    </>
  );
}

/** One run's artifacts and the gates it waits at, for a run that belongs to no ticket. */
export function RunReviewPage() {
  const { slug } = useParams({ strict: false }) as { slug: string };
  const search = useSearch({ strict: false }) as { artifact?: string; returnTo?: string };
  const record = useRunReviews(slug);
  const runs = useRun(slug, record.data?.run.sessions);
  const run = runs.run;
  const publications = useRunPublications(run);
  const ticketRef = record.data?.run.ticket ?? null;
  const project = useWorkDetail(ticketRef ? 'project' : null, ticketRef?.projectId ?? null);
  const ticket =
    project.data?.tasks.find((task) => task.source.issueId === ticketRef?.issueId) ?? null;
  // A ticket's run opened from nowhere in particular goes back to the ticket's board.
  const home: ReviewBack =
    ticketRef && project.data
      ? {
          pathname: `/ting/work/project:${ticketRef.projectId}`,
          search: { view: 'board' },
          label: `Back to the ${project.data.item.title} board`,
        }
      : ARTIFACTS_HOME;
  const back = reviewBack(search.returnTo, home);

  if (record.isLoading || runs.loading || publications.isLoading) {
    return <p className="ting-review__state">Loading the run…</p>;
  }
  if (record.error || !record.data) {
    return (
      <p className="ting-review__state" role="alert">
        {record.error instanceof Error ? record.error.message : 'This run could not be read.'}
      </p>
    );
  }
  if (!run) {
    return (
      <p className="ting-review__state" role="alert">
        {runs.error instanceof Error ? runs.error.message : `Run ${slug} could not be read.`}
      </p>
    );
  }
  if (publications.error) {
    return (
      <p className="ting-review__state" role="alert">
        What the run presented could not be read:{' '}
        {publications.error instanceof Error
          ? publications.error.message
          : String(publications.error)}
      </p>
    );
  }
  const { unreadableRuns } = record.data;
  return (
    <div className="ting-review-page">
      {unreadableRuns.length > 0 ? (
        <p className="ting-review__error" role="alert">
          The session of {unreadableRuns.join(', ')} is gone, so its review cannot be read.
        </p>
      ) : null}
      <RunWorkspace
        record={record.data}
        run={run}
        ticket={ticket}
        board={project.data?.board ?? null}
        publications={publications.data ?? []}
        initialReview={search.artifact ?? null}
        back={back}
      />
    </div>
  );
}

function SessionWorkspace({
  session,
  publications,
  initialReview,
  back,
}: {
  session: SessionRef;
  publications: readonly Publication[];
  initialReview: string | null;
  back: ReviewBack;
}) {
  const message = useSendToRun();
  const artifacts = useMemo(() => sessionArtifacts(session, publications), [session, publications]);
  const read = useArtifactTexts(artifacts);
  const reviews = artifacts.map((artifact) => artifactReview(artifact, [], read.texts));

  // Notes reach a session only as a message while it runs; a session starts no revision run.
  function send(review: RunReview): ReviewSend | null {
    const artifact = artifacts.find((item) => item.id === review.columnId);
    if (!artifact || !session.running) return null;
    return {
      title: 'Send back to the session',
      hint: 'The session is running. Your notes arrive in its chat as your next message.',
      label: 'Send to the session',
      busy: message.isPending,
      failure: message.error,
      onSend: (notes, onSuccess) =>
        message.mutate({ session, content: noteForRun(artifact, notes) }, { onSuccess }),
    };
  }

  if (reviews.length === 0) {
    return (
      <p className="ting-review__state">
        {session.title} has presented nothing.{' '}
        <Link to={'/ting/artifacts' as never}>Back to Artifacts</Link>
      </p>
    );
  }
  return (
    <>
      {textFailures(read.failed)}
      <ReviewWorkspace
        subject={{
          identifier: null,
          kind: 'Session',
          title: session.title,
          gate: null,
          pullRequestUrl: null,
          canAsk: false,
        }}
        board={null}
        reviews={reviews}
        initialReview={initialReview}
        back={back}
        railLabel="Session"
        rail={
          <>
            <span>{session.running ? 'Running' : 'Not running'}</span>
            <Link to={`/volundr/sessions/${session.sessionId}` as never}>Open the session</Link>
          </>
        }
        deciding={false}
        decisionFailure={null}
        onDecide={() => undefined}
        send={send}
      />
    </>
  );
}

/** What one session that is no workflow run presented, on the same review page as a run. */
export function SessionArtifactsPage() {
  const { sessionId } = useParams({ strict: false }) as { sessionId: string };
  const search = useSearch({ strict: false }) as {
    artifact?: string;
    instance?: string;
    returnTo?: string;
  };
  const instanceId = search.instance ?? null;
  const session = useArtifactSession(sessionId, instanceId);
  const publications = useSessionPublications(
    { sessionId, instanceId },
    session.data?.running ?? false,
  );

  if (session.isLoading || publications.isLoading) {
    return <p className="ting-review__state">Loading the session…</p>;
  }
  const error = session.error ?? publications.error;
  if (error || !session.data) {
    return (
      <p className="ting-review__state" role="alert">
        What the session presented could not be read:{' '}
        {error instanceof Error ? error.message : String(error)}
      </p>
    );
  }
  return (
    <div className="ting-review-page">
      <SessionWorkspace
        session={session.data}
        publications={publications.data ?? []}
        initialReview={search.artifact ?? null}
        back={reviewBack(search.returnTo, ARTIFACTS_HOME)}
      />
    </div>
  );
}
