import './TicketReviewPage.css';
import { useMemo } from 'react';
import { Link, useNavigate, useParams, useSearch } from '@tanstack/react-router';
import { isPending, type RunReview, type RunReviews } from '../../domain/review';
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
} from '../../domain/artifacts';
import { useRunGateDecision, useRunReviews } from '../useWork';
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
import { ReviewWorkspace, type ReviewSend } from './ReviewWorkspace';

/** A run has no board to ask, so its waiting gate is the one its log shows as pending. */
function pendingGate(reviews: readonly RunReview[]) {
  const revision = reviews.flatMap((review) => review.revisions).find(isPending);
  return revision ? { id: revision.gateId, activationId: revision.activationId } : null;
}

function runEnded(run: RunRef): boolean {
  return run.condition === 'completed' || run.condition === 'failed';
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

function RunWorkspace({
  record,
  run,
  publications,
  initialReview,
}: {
  record: RunReviews;
  run: RunRef;
  publications: readonly Publication[];
  initialReview: string | null;
}) {
  const navigate = useNavigate();
  const waiting = useWaiting();
  const decide = useRunGateDecision(record.run.slug);
  const message = useSendToRun(run);
  const revise = useStartRevision();
  const gate = pendingGate(record.reviews);
  const brief = record.run.prompt;

  const presented = useMemo(
    () => artifactsFromPublications(run, publications, waiting),
    [run, publications, waiting],
  );
  const read = useArtifactTexts(run, presented);
  // A gate that shows an artifact reads it in its own panel; the rest get a tab each.
  const shownByGates = artifactKeysShown(record.reviews);
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
        hint: 'The run is still working. Your notes arrive in its chat as your next message.',
        label: 'Send to the run',
        busy: message.isPending,
        failure: message.error,
        onSend: (notes, onSuccess) => message.mutate(noteForRun(artifact, notes), { onSuccess }),
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

  if (reviews.length === 0) {
    return (
      <p className="ting-review__state">
        {record.run.workflowName} has produced nothing to review yet.{' '}
        <Link to={'/ting/artifacts' as never}>Back to Artifacts</Link>
      </p>
    );
  }
  return (
    <>
      {unread}
      <ReviewWorkspace
        subject={{
          identifier: null,
          title: record.run.workflowName,
          gate,
          pullRequestUrl: null,
          canAsk: false,
        }}
        board={null}
        reviews={reviews}
        initialReview={initialReview}
        railLabel="Run"
        rail={
          <>
            {brief ? (
              <details className="ting-review__brief">
                <summary>What it was asked to do</summary>
                <p>{brief}</p>
              </details>
            ) : null}
            {record.run.startedBy ? <span>Started by {record.run.startedBy}</span> : null}
            {record.run.branch ? <span>On {record.run.branch}</span> : null}
            <Link to={`/volundr/sessions/${record.run.sessionId}` as never}>Open the session</Link>
          </>
        }
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
  const search = useSearch({ strict: false }) as { artifact?: string };
  const record = useRunReviews(slug);
  const runs = useRun(slug);
  const run = runs.run;
  const publications = useRunPublications(run);

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
        {runs.error instanceof Error
          ? runs.error.message
          : `Run ${slug} is not among the runs Work lists, so its session cannot be found.`}
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
        publications={publications.data ?? []}
        initialReview={search.artifact ?? null}
      />
    </div>
  );
}

function SessionWorkspace({
  session,
  publications,
  initialReview,
}: {
  session: SessionRef;
  publications: readonly Publication[];
  initialReview: string | null;
}) {
  const message = useSendToRun(session);
  const artifacts = useMemo(() => sessionArtifacts(session, publications), [session, publications]);
  const read = useArtifactTexts(session, artifacts);
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
      onSend: (notes, onSuccess) => message.mutate(noteForRun(artifact, notes), { onSuccess }),
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
  const search = useSearch({ strict: false }) as { artifact?: string; instance?: string };
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
      />
    </div>
  );
}
