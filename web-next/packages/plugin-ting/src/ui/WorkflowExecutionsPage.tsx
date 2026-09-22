import { useEffect, useRef, useState } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { randomId } from '@niuulabs/ui';
import type {
  IWorkflowExecutionService,
  IDeliveryExecutionService,
  IWorkflowService,
} from '../ports';
import type {
  WorkflowExecution,
  DeliveryExecution,
  WorkflowExecutionLaunch,
  DeliveryExecutionLaunch,
} from '../domain/workflowExecution';
import { executionIsTerminal } from '../domain/workflowExecution';
import { subworkflowNodes, workflowRequiresDeliveryPack } from '../domain/workflow';
import { buildWorkflowExecutionResultsMarkdown } from '../application/workflowExecutionResults';
import { WorkflowResults } from './WorkflowResults';
import { WorkflowExecutionTraceGraph } from './WorkflowExecutionTraceGraph';

const inputClass =
  'niuu:w-full niuu:rounded niuu:border niuu:border-border niuu:bg-bg-secondary niuu:p-2 niuu:text-text-primary';
const buttonClass =
  'niuu:rounded niuu:border niuu:border-border niuu:px-3 niuu:py-2 niuu:disabled:opacity-50';

type LaunchRequest =
  | { kind: 'generic'; request: WorkflowExecutionLaunch }
  | { kind: 'delivery'; request: DeliveryExecutionLaunch };

export function WorkflowExecutionsPage() {
  const workflowExecutions = useService<IWorkflowExecutionService>('ting.workflowExecutions');
  const deliveryExecutions = useService<IDeliveryExecutionService>('ting.deliveryExecutions');
  const workflows = useService<IWorkflowService>('ting.workflows');
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState('');
  const [selectedWorkflowId, setSelectedWorkflowId] = useState('');
  const [workflowId, setWorkflowId] = useState('');
  const [parentNodeId, setParentNodeId] = useState('');
  const [prompt, setPrompt] = useState('');
  const [repo, setRepo] = useState('');
  const [baseBranch, setBaseBranch] = useState('');
  const [model, setModel] = useState('');
  const [connectionId, setConnectionId] = useState('');
  const [showEvidence, setShowEvidence] = useState(false);
  const [showGraph, setShowGraph] = useState(false);
  const focusEvidence = useRef(false);
  const evidenceRegion = useRef<HTMLDivElement>(null);
  const launchAttempt = useRef<{ fingerprint: string; idempotencyKey: string } | null>(null);
  const catalog = useQuery({
    queryKey: ['ting', 'workflows'],
    queryFn: () => workflows.listWorkflows(),
  });
  const launchableWorkflows =
    catalog.data?.filter((workflow) => workflow.schemaVersion === 2) ?? [];
  const selectedWorkflow = catalog.data?.find((workflow) => workflow.id === workflowId);
  const expansionNodes = selectedWorkflow ? subworkflowNodes(selectedWorkflow) : [];
  const isDeliveryWorkflow = selectedWorkflow
    ? workflowRequiresDeliveryPack(selectedWorkflow)
    : false;
  const selectedRunWorkflow = catalog.data?.find((item) => item.id === selectedWorkflowId);
  const isDeliverySelected = selectedRunWorkflow
    ? workflowRequiresDeliveryPack(selectedRunWorkflow)
    : false;

  const runs = useInfiniteQuery({
    queryKey: ['ting', 'workflow-executions', 'pages'],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      workflowExecutions.list(pageParam ? { cursor: pageParam } : undefined),
    getNextPageParam: (page) => page.nextCursor ?? undefined,
    refetchInterval: 5000,
  });
  const visibleRuns = [
    ...new Map(
      runs.data?.pages.flatMap((page) => page.executions).map((run) => [run.executionId, run]),
    ).values(),
  ];
  const selected = useQuery({
    queryKey: ['ting', 'workflow-execution', selectedId],
    queryFn: () => workflowExecutions.get(selectedId),
    enabled: !!selectedId,
    refetchInterval: (query) =>
      query.state.data && executionIsTerminal(query.state.data.state) ? false : 3000,
  });
  const selectedDelivery = useQuery({
    queryKey: ['ting', 'delivery-execution', selectedId],
    queryFn: () => deliveryExecutions.get(selectedId),
    enabled: !!selectedId && isDeliverySelected && showEvidence,
    refetchInterval: (query) =>
      query.state.data && executionIsTerminal(query.state.data.state) ? false : 3000,
  });
  const resultsScope =
    selected.data && executionIsTerminal(selected.data.state)
      ? `terminal:${selected.data.state}:${selected.data.updatedAt}`
      : 'active';
  const waits = useQuery({
    queryKey: ['ting', 'workflow-execution-waits', selectedId, resultsScope],
    queryFn: () => workflowExecutions.waits(selectedId),
    enabled: !!selectedId && showEvidence,
    refetchInterval: selected.data && !executionIsTerminal(selected.data.state) ? 3000 : false,
  });
  const evidence = useQuery({
    queryKey: ['ting', 'delivery-evidence', selectedId, resultsScope],
    queryFn: () => deliveryExecutions.evidence(selectedId),
    enabled: !!selectedId && showEvidence && isDeliverySelected,
    refetchInterval: selected.data && !executionIsTerminal(selected.data.state) ? 3000 : false,
  });
  useEffect(() => {
    const deliveryReady = !isDeliverySelected || (evidence.data && selectedDelivery.data);
    if (!focusEvidence.current || !showEvidence || !waits.data || !deliveryReady) return;
    evidenceRegion.current?.scrollIntoView({ block: 'start', behavior: 'smooth' });
    evidenceRegion.current?.focus({ preventScroll: true });
    focusEvidence.current = false;
  }, [showEvidence, waits.data, evidence.data, selectedDelivery.data, isDeliverySelected]);
  const selectRun = (run: { executionId: string; workflowId: string }) => {
    setSelectedId(run.executionId);
    setSelectedWorkflowId(run.workflowId);
    setShowEvidence(false);
    setShowGraph(false);
    focusEvidence.current = false;
  };
  const invalidateRunQueries = (executionId: string, terminal: boolean) => {
    void queryClient.invalidateQueries({ queryKey: ['ting', 'workflow-executions'] });
    if (terminal) return;
    void queryClient.invalidateQueries({
      queryKey: ['ting', 'workflow-execution-waits', executionId],
    });
    void queryClient.invalidateQueries({ queryKey: ['ting', 'delivery-evidence', executionId] });
  };
  const launch = useMutation<WorkflowExecution | DeliveryExecution, Error, LaunchRequest>({
    mutationFn: (input: LaunchRequest) => {
      const fingerprint = JSON.stringify(input);
      if (launchAttempt.current?.fingerprint !== fingerprint) {
        launchAttempt.current = { fingerprint, idempotencyKey: randomId() };
      }
      return input.kind === 'delivery'
        ? deliveryExecutions.launch(input.request, launchAttempt.current.idempotencyKey)
        : workflowExecutions.launch(input.request, launchAttempt.current.idempotencyKey);
    },
    onSuccess: (run: WorkflowExecution | DeliveryExecution, input) => {
      launchAttempt.current = null;
      setSelectedId(run.executionId);
      setSelectedWorkflowId(run.workflowId);
      if (input.kind === 'delivery') {
        queryClient.setQueryData(['ting', 'delivery-execution', run.executionId], run);
        void queryClient.invalidateQueries({
          queryKey: ['ting', 'workflow-execution', run.executionId],
        });
      } else {
        queryClient.setQueryData(['ting', 'workflow-execution', run.executionId], run);
      }
      invalidateRunQueries(run.executionId, executionIsTerminal(run.state));
    },
  });
  const changeLaunchField = (change: () => void) => {
    launchAttempt.current = null;
    change();
  };
  const handleWorkflowChange = (id: string) => {
    changeLaunchField(() => {
      setWorkflowId(id);
      const workflow = catalog.data?.find((item) => item.id === id);
      const nodes = workflow ? subworkflowNodes(workflow) : [];
      setParentNodeId(nodes.length === 1 ? nodes[0]!.id : '');
    });
  };
  const action = useMutation({
    mutationFn: (
      request:
        | { operation: 'cancel' | 'reconcile' }
        | { operation: 'retry'; childKey: string; attemptId: string },
    ) =>
      request.operation === 'retry'
        ? workflowExecutions.retry(selectedId, request.childKey, request.attemptId)
        : workflowExecutions[request.operation](selectedId),
    onSuccess: (run: WorkflowExecution) => {
      queryClient.setQueryData(['ting', 'workflow-execution', run.executionId], run);
      if (isDeliverySelected) {
        void queryClient.invalidateQueries({
          queryKey: ['ting', 'delivery-execution', run.executionId],
        });
      }
      invalidateRunQueries(run.executionId, executionIsTerminal(run.state));
    },
  });
  const error =
    launch.error ??
    action.error ??
    runs.error ??
    selected.error ??
    selectedDelivery.error ??
    catalog.error ??
    evidence.error ??
    waits.error;
  const errorMessage =
    error && 'detail' in error && typeof error.detail === 'string' ? error.detail : error?.message;

  return (
    <main className="niuu:p-6 niuu:space-y-6 niuu:text-text-primary">
      <header>
        <h1 className="niuu:text-xl niuu:font-semibold">Developer workflows</h1>
        <p className="niuu:text-text-secondary">
          Plan, review, implement, verify, and deliver a ticket.
        </p>
      </header>
      {error && (
        <p role="alert" className="niuu:text-critical">
          {errorMessage}
        </p>
      )}
      <form
        aria-label="Launch developer workflow"
        className="niuu:grid niuu:gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (!selectedWorkflow || !parentNodeId) return;
          if (isDeliveryWorkflow) {
            launch.mutate({
              kind: 'delivery',
              request: {
                workflowId,
                parentNodeId,
                prompt,
                repo,
                baseBranch,
                ...(model ? { model } : {}),
                ...(connectionId ? { connectionId } : {}),
              },
            });
            return;
          }
          launch.mutate({
            kind: 'generic',
            request: {
              workflowId,
              parentNodeId,
              prompt,
              ...(model ? { model } : {}),
              ...(connectionId ? { connectionId } : {}),
            },
          });
        }}
      >
        <label>
          Workflow
          <select
            required
            className={inputClass}
            value={workflowId}
            onChange={(event) => handleWorkflowChange(event.target.value)}
          >
            <option value="">Choose a developer workflow</option>
            {launchableWorkflows.map((workflow) => (
              <option key={workflow.id} value={workflow.id}>
                {workflow.name}
              </option>
            ))}
          </select>
        </label>
        {catalog.isPending ? (
          <p role="status">Loading workflows…</p>
        ) : !catalog.isError && launchableWorkflows.length === 0 ? (
          <p>No developer workflows are installed.</p>
        ) : null}
        {selectedWorkflow && expansionNodes.length === 0 && (
          <p role="alert">
            This workflow has no subworkflow expansion point; it cannot be launched from here.
          </p>
        )}
        {expansionNodes.length > 1 && (
          <label>
            Expansion point
            <select
              required
              className={inputClass}
              value={parentNodeId}
              onChange={(event) => changeLaunchField(() => setParentNodeId(event.target.value))}
            >
              <option value="">Choose an expansion point</option>
              {expansionNodes.map((node) => (
                <option key={node.id} value={node.id}>
                  {node.label}
                </option>
              ))}
            </select>
          </label>
        )}
        {expansionNodes.length === 1 && (
          <p className="niuu:text-text-secondary">Expansion point: {expansionNodes[0]!.label}</p>
        )}
        <label>
          Ticket or objective
          <textarea
            required
            className={inputClass}
            rows={4}
            value={prompt}
            onChange={(event) => changeLaunchField(() => setPrompt(event.target.value))}
          />
        </label>
        {isDeliveryWorkflow && (
          <>
            <label>
              Repository
              <input
                required
                className={inputClass}
                value={repo}
                onChange={(event) => changeLaunchField(() => setRepo(event.target.value))}
                placeholder="Repository URL"
              />
            </label>
            <label>
              Target branch
              <input
                required
                className={inputClass}
                value={baseBranch}
                onChange={(event) => changeLaunchField(() => setBaseBranch(event.target.value))}
              />
            </label>
          </>
        )}
        <details>
          <summary>Runtime settings</summary>
          <label>
            Model
            <input
              className={inputClass}
              value={model}
              onChange={(event) => changeLaunchField(() => setModel(event.target.value))}
              placeholder="Configured default"
            />
          </label>
          <label>
            Connection
            <input
              className={inputClass}
              value={connectionId}
              onChange={(event) => changeLaunchField(() => setConnectionId(event.target.value))}
              placeholder="Configured default"
            />
          </label>
        </details>
        <button
          type="submit"
          className={buttonClass}
          disabled={launch.isPending || !workflowId || !parentNodeId}
        >
          {launch.isPending ? 'Starting…' : 'Start workflow'}
        </button>
      </form>
      <section aria-label="Developer workflow runs" className="niuu:space-y-3">
        <h2 className="niuu:text-lg niuu:font-semibold">Runs</h2>
        {runs.isPending ? (
          <p role="status">Loading runs…</p>
        ) : !runs.isError && visibleRuns.length === 0 ? (
          <p>No developer workflow runs yet.</p>
        ) : null}
        <ul className="niuu:space-y-2">
          {visibleRuns.map((run) => (
            <li key={run.executionId}>
              <button
                className={buttonClass}
                aria-pressed={selectedId === run.executionId}
                onClick={() => selectRun(run)}
              >
                {run.name} · {run.state}
              </button>
            </li>
          ))}
        </ul>
        {runs.hasNextPage && (
          <button
            className={buttonClass}
            disabled={runs.isFetching}
            onClick={() => void runs.fetchNextPage()}
          >
            {runs.isFetchingNextPage ? 'Loading more runs…' : 'Load more runs'}
          </button>
        )}
      </section>
      {selectedId && selected.isPending && <p role="status">Loading run…</p>}
      {selected.data && (
        <section aria-label="Selected developer workflow" className="niuu:space-y-3">
          <h2 className="niuu:text-lg niuu:font-semibold">{selected.data.name}</h2>
          <p>
            {selected.data.state} · Generation {selected.data.currentGeneration}
          </p>
          <p>{selected.data.suspensionReason}</p>
          <p>
            Budget: {selected.data.budget.spentUnits} spent, {selected.data.budget.reservedUnits}{' '}
            reserved, {selected.data.budget.availableUnits} available
          </p>
          <div className="niuu:flex niuu:gap-2">
            <button
              className={buttonClass}
              disabled={action.isPending || executionIsTerminal(selected.data.state)}
              onClick={() => action.mutate({ operation: 'reconcile' })}
            >
              Refresh task status
            </button>
            <button
              className={buttonClass}
              disabled={
                action.isPending ||
                executionIsTerminal(selected.data.state) ||
                selected.data.state === 'canceling'
              }
              onClick={() => action.mutate({ operation: 'cancel' })}
            >
              Cancel run
            </button>
            <button className={buttonClass} onClick={() => setShowEvidence(!showEvidence)}>
              {showEvidence ? 'Hide evidence' : 'View evidence'}
            </button>
            <button
              className={buttonClass}
              onClick={() => setShowGraph(!showGraph)}
              aria-expanded={showGraph}
            >
              {showGraph ? 'Hide execution graph' : 'View execution graph'}
            </button>
          </div>
          {showGraph && (
            <WorkflowExecutionTraceGraph
              key={selected.data.executionId}
              execution={selected.data}
              onOpenEvidence={() => {
                setShowEvidence(true);
                focusEvidence.current = true;
                if (evidenceRegion.current) {
                  evidenceRegion.current.scrollIntoView({ block: 'start', behavior: 'smooth' });
                  evidenceRegion.current.focus({ preventScroll: true });
                  focusEvidence.current = false;
                }
              }}
            />
          )}
          <table className="niuu:w-full niuu:text-left">
            <caption className="niuu:text-left">Workstreams and attempts</caption>
            <thead>
              <tr>
                {['Workstream', 'Attempt', 'State', 'Dependencies', 'Task', 'Actions'].map(
                  (label) => (
                    <th key={label} scope="col">
                      {label}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {selected.data.children.map((child) => (
                <tr key={child.childId}>
                  <th scope="row">{child.childKey}</th>
                  <td>{child.attempt}</td>
                  <td>
                    {child.state}
                    {child.error && <p className="niuu:text-critical">{child.error.detail}</p>}
                    {child.pendingQuestions?.map((question) => (
                      <div key={question.requestId} className="niuu:my-2 niuu:space-y-1">
                        <p className="niuu:font-medium">{question.question}</p>
                        {question.reason && <p>{question.reason}</p>}
                        {question.recommendation && (
                          <p>Recommendation: {question.recommendation}</p>
                        )}
                      </div>
                    ))}
                    {child.pendingGates?.map((gate) => (
                      <div key={gate.gateId} className="niuu:my-2 niuu:space-y-1">
                        <p className="niuu:font-medium">{gate.label}</p>
                        {gate.summary && <p>{gate.summary}</p>}
                        {gate.instructions && <p>{gate.instructions}</p>}
                      </div>
                    ))}
                  </td>
                  <td>{child.dependencies.join(', ') || 'None'}</td>
                  <td>{child.taskHandle?.taskId || 'Pending launch'}</td>
                  <td>
                    {(child.state === 'failed' || child.state === 'blocked') && (
                      <button
                        className={buttonClass}
                        disabled={action.isPending || executionIsTerminal(selected.data.state)}
                        onClick={() =>
                          action.mutate({
                            operation: 'retry',
                            childKey: child.childKey,
                            attemptId: child.childId,
                          })
                        }
                      >
                        Retry {child.childKey}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {showEvidence && (
            <div ref={evidenceRegion} tabIndex={-1} className="niuu:space-y-3">
              <section aria-label="Execution waits" className="niuu:space-y-2">
                <h3 className="niuu:text-lg niuu:font-semibold">Waits</h3>
                {waits.isPending ? (
                  <p role="status">Loading waits…</p>
                ) : waits.data && waits.data.length === 0 ? (
                  <p>No waits have been recorded for this execution.</p>
                ) : (
                  <ul className="niuu:space-y-1">
                    {waits.data?.map((wait) => (
                      <li key={wait.waitId}>
                        {wait.nodeId} · {wait.conditionType} · {wait.state}
                        {wait.observation && <> · {wait.observation.status}</>}
                      </li>
                    ))}
                  </ul>
                )}
              </section>
              {isDeliverySelected &&
                (evidence.isPending || selectedDelivery.isPending || waits.isPending ? (
                  <p role="status">Loading results…</p>
                ) : (
                  evidence.data &&
                  selectedDelivery.data &&
                  waits.data && (
                    <div className="niuu:space-y-3">
                      <WorkflowResults
                        ariaLabel="Execution evidence"
                        headingLevel={3}
                        title="Workflow results"
                        status={selectedDelivery.data.state}
                        filename={`${selectedDelivery.data.executionId}-results.md`}
                        context={[
                          {
                            label: 'Generation',
                            value: String(selectedDelivery.data.currentGeneration),
                          },
                          {
                            label: 'Plan revision',
                            value: selectedDelivery.data.planRevision ?? 'Pending',
                          },
                          {
                            label: 'Recorded attempts',
                            value: String(selectedDelivery.data.children.length),
                          },
                          {
                            label: 'Updated',
                            value: selectedDelivery.data.updatedAt || 'Not reported',
                          },
                        ]}
                        markdown={buildWorkflowExecutionResultsMarkdown(
                          selectedDelivery.data,
                          evidence.data,
                          waits.data,
                        )}
                      />
                      <details className="niuu:rounded niuu:border niuu:border-border niuu:bg-bg-secondary niuu:p-4">
                        <summary className="niuu:cursor-pointer niuu:font-medium">
                          Raw evidence JSON
                        </summary>
                        <pre
                          aria-label="Raw execution evidence"
                          className="niuu:mt-3 niuu:overflow-auto niuu:text-xs"
                        >
                          {JSON.stringify(
                            {
                              execution: selectedDelivery.data,
                              evidence: evidence.data,
                              waits: waits.data,
                            },
                            null,
                            2,
                          )}
                        </pre>
                      </details>
                    </div>
                  )
                ))}
            </div>
          )}
        </section>
      )}
    </main>
  );
}
