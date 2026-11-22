import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useOptionalService, useService } from '@niuulabs/plugin-sdk';
import type { IRunLogService, IWorkService } from '../ports';
import { runGraphIsMoving } from '../domain/runGraph';
import { sessionHereQuery } from './useSessionHere';
import { GraphView } from './WorkflowBuilder/GraphView';
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
  const runLog = useOptionalService<IRunLogService>('ting.runLog');
  const queryClient = useQueryClient();
  // A member a node does not have: Ting runs members on any host, a node opens its own.
  const [elsewhere, setElsewhere] = useState(false);
  const openMember = async (sessionId: string) => {
    const here =
      runLog?.scope !== 'node' ||
      (await queryClient
        .fetchQuery(sessionHereQuery(runLog, sessionId))
        // The session's own page shows what is wrong with it.
        .catch(() => true));
    setElsewhere(!here);
    if (here) onOpenSession?.(sessionId);
  };
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
      {elsewhere ? (
        <p className="ting-run-graph__status" role="status">
          That session runs on another host, so it does not open here.
        </p>
      ) : null}
      <GraphView
        readOnly
        fitOnLoad
        readOnlyMessage="This is a run: it follows the workflow version it was launched with."
        nodes={graph.data.nodes}
        edges={graph.data.edges}
        runStates={graph.data.states}
        onOpenMemberSession={onOpenSession ? (id) => void openMember(id) : undefined}
        selectedNodeId={selectedNodeId}
        connectingFromId={null}
        onSelectNode={setSelectedNodeId}
        onInspectNode={setSelectedNodeId}
        onAddNode={noop}
        onDeleteNode={noop}
        onDeleteEdge={noop}
        onStartConnect={noop}
        onCancelConnect={noop}
        onCompleteConnect={noop}
      />
    </div>
  );
}
