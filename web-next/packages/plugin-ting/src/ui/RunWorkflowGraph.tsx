import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { GateDecision } from '@niuulabs/ui';
import type { IWorkService } from '../ports';
import type { RunGraph } from '../domain/runGraph';
import { runGraphIsMoving } from '../domain/runGraph';
import type { RunReviews } from '../domain/review';
import { GraphView } from './WorkflowBuilder/GraphView';
import { decisionError } from './TicketPanel';
import { useRunGateDecision } from './useWork';
import './RunWorkflowGraph.css';

/** While a run has work going on or waiting, its graph is read again this often. */
const RUN_GRAPH_REFRESH_MS = 3000;

const noop = () => {};

export interface RunWorkflowGraphProps {
  /** The run's slug. */
  runId: string;
  /** Open a member's session from its chip on the graph. */
  onOpenSession?: (sessionId: string) => void;
}

/**
 * A run drawn on its workflow, read only: the graph it was launched with, each node with
 * where the run is at it and the members that took it up. It follows the run while the run
 * is moving and stops reading once nothing at any node can change.
 */
export function RunWorkflowGraph({ runId, onOpenSession }: RunWorkflowGraphProps) {
  const work = useService<IWorkService>('ting.work');
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const graph = useQuery({
    queryKey: ['ting', 'work', 'run-graph', runId],
    queryFn: () => work.runGraph(runId),
    refetchInterval: (query) =>
      query.state.data && runGraphIsMoving(query.state.data) ? RUN_GRAPH_REFRESH_MS : false,
  });

  if (graph.isPending) {
    return (
      <div className="ting-run-graph__status" data-testid="run-graph-loading">
        Loading the run&apos;s workflow…
      </div>
    );
  }
  if (graph.isError) {
    return (
      <div className="ting-run-graph__status" role="alert" data-testid="run-graph-error">
        The run&apos;s workflow could not be read: {String(graph.error)}
      </div>
    );
  }
  return (
    <div className="ting-run-graph" data-testid="run-graph">
      {waitingGate(graph.data) ? <WaitingGate runId={runId} /> : null}
      <GraphView
        readOnly
        fitOnLoad
        readOnlyMessage="This is a run: it follows the workflow version it was launched with."
        nodes={graph.data.nodes}
        edges={graph.data.edges}
        runStates={graph.data.states}
        onOpenMemberSession={onOpenSession}
        selectedNodeId={selectedNodeId}
        connectingFromId={null}
        onSelectNode={setSelectedNodeId}
        onInspectNode={setSelectedNodeId}
        onAddNode={noop}
        onDeleteNode={noop}
        onDeleteEdge={noop}
        onMoveNode={noop}
        onStartConnect={noop}
        onCancelConnect={noop}
        onCompleteConnect={noop}
      />
    </div>
  );
}

/** Whether a gate of the run waits for a person. */
function waitingGate(graph: RunGraph): boolean {
  return graph.nodes.some(
    (node) => node.kind === 'gate' && graph.states.get(node.id)?.state === 'waiting',
  );
}

/** The gate waiting for a person, read from the run's reviews: its newest pending revision. */
function pendingGate(reviews: RunReviews | undefined) {
  for (const review of reviews?.reviews ?? []) {
    const latest = review.revisions[review.revisions.length - 1];
    if (latest?.status === 'pending') return { review, revision: latest };
  }
  return null;
}

/**
 * Where the run says Needs you, the person answers: the gate's summary, a note, approve or
 * request changes, as the review page has them, and the review page for the full context.
 */
function WaitingGate({ runId }: { runId: string }) {
  const work = useService<IWorkService>('ting.work');
  const reviews = useQuery({
    queryKey: ['ting', 'work', 'reviews', 'run', runId],
    queryFn: () => work.runReviews(runId),
  });
  const decide = useRunGateDecision(runId);
  const [note, setNote] = useState('');
  const waiting = pendingGate(reviews.data);
  if (reviews.isError) {
    return (
      <div className="ting-run-graph__gate" role="alert" data-testid="run-gate-error">
        The waiting gate could not be read: {String(reviews.error)}
      </div>
    );
  }
  if (!waiting) return null;
  const { review, revision } = waiting;
  const submit = (decision: 'approve' | 'request_changes') =>
    decide.mutate(
      { gateId: revision.gateId, activationId: revision.activationId, decision, notes: note },
      { onSuccess: () => setNote('') },
    );
  return (
    <div className="ting-run-graph__gate" data-testid="run-gate">
      <GateDecision
        eyebrow="Needs you"
        title={review.title}
        summary={revision.summary}
        hint={
          <>
            Approving continues the run; changes go back to the agent.{' '}
            <a href={`/ting/review/run/${encodeURIComponent(runId)}`}>Open the review</a>
          </>
        }
        note={note}
        onNoteChange={setNote}
        notePlaceholder="Anything you want changed."
        requireNote
        busy={decide.isPending}
        error={decide.error ? decisionError(decide.error) : null}
        onApprove={() => submit('approve')}
        onRequestChanges={() => submit('request_changes')}
      />
    </div>
  );
}
