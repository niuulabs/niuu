import { useEffect, useRef, useState } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { randomId } from '@niuulabs/ui';
import type { IDeliveryExecutionService, IWorkflowService } from '../ports';
import type { WorkflowExecution, WorkflowExecutionLaunch } from '../domain/workflowExecution';
import { executionIsTerminal } from '../domain/workflowExecution';
import { buildWorkflowExecutionResultsMarkdown } from '../application/workflowExecutionResults';
import { WorkflowResults } from './WorkflowResults';
import { WorkflowExecutionTraceGraph } from './WorkflowExecutionTraceGraph';

const inputClass =
  'niuu:w-full niuu:rounded niuu:border niuu:border-border niuu:bg-bg-secondary niuu:p-2 niuu:text-text-primary';
const buttonClass =
  'niuu:rounded niuu:border niuu:border-border niuu:px-3 niuu:py-2 niuu:disabled:opacity-50';

export function WorkflowExecutionsPage() {
  const service = useService<IDeliveryExecutionService>('ting.workflowExecutions');
  const workflows = useService<IWorkflowService>('ting.workflows');
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState('');
  const [workflowId, setWorkflowId] = useState('');
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
  const runs = useInfiniteQuery({
    queryKey: ['ting', 'workflow-executions', 'pages'],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => service.list(pageParam ? { cursor: pageParam } : undefined),
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
    queryFn: () => service.get(selectedId),
    enabled: !!selectedId,
    refetchInterval: (query) =>
      query.state.data && executionIsTerminal(query.state.data.state) ? false : 3000,
  });
  const resultsScope =
    selected.data && executionIsTerminal(selected.data.state)
      ? `terminal:${selected.data.state}:${selected.data.updatedAt}`
      : 'active';
  const evidence = useQuery({
    queryKey: ['ting', 'developer-evidence', selectedId, resultsScope],
    queryFn: () => service.evidence(selectedId),
    enabled: !!selectedId && showEvidence,
    refetchInterval: selected.data && !executionIsTerminal(selected.data.state) ? 3000 : false,
  });
  const deliveryWaits = useQuery({
    queryKey: ['ting', 'developer-delivery-waits', selectedId, resultsScope],
    queryFn: () => service.deliveryWaits(selectedId),
    enabled: !!selectedId && showEvidence,
    refetchInterval: selected.data && !executionIsTerminal(selected.data.state) ? 3000 : false,
  });
  useEffect(() => {
    if (!focusEvidence.current || !showEvidence || !evidence.data || !deliveryWaits.data) return;
    evidenceRegion.current?.scrollIntoView({ block: 'start', behavior: 'smooth' });
    evidenceRegion.current?.focus({ preventScroll: true });
    focusEvidence.current = false;
  }, [showEvidence, evidence.data, deliveryWaits.data]);
  const update = (run: WorkflowExecution) => {
    setSelectedId(run.executionId);
    queryClient.setQueryData(['ting', 'workflow-execution', run.executionId], run);
    void queryClient.invalidateQueries({ queryKey: ['ting', 'workflow-executions'] });
    if (!executionIsTerminal(run.state)) {
      void queryClient.invalidateQueries({
        queryKey: ['ting', 'developer-evidence', run.executionId],
      });
      void queryClient.invalidateQueries({
        queryKey: ['ting', 'developer-delivery-waits', run.executionId],
      });
    }
  };
  const launch = useMutation({
    mutationFn: (request: WorkflowExecutionLaunch) => {
      const fingerprint = JSON.stringify(request);
      if (launchAttempt.current?.fingerprint !== fingerprint) {
        launchAttempt.current = { fingerprint, idempotencyKey: randomId() };
      }
      return service.launch(request, launchAttempt.current.idempotencyKey);
    },
    onSuccess: (run) => {
      launchAttempt.current = null;
      update(run);
    },
  });
  const changeLaunchField = (change: () => void) => {
    launchAttempt.current = null;
    change();
  };
  const action = useMutation({
    mutationFn: (
      request:
        | { operation: 'cancel' | 'reconcile' }
        | { operation: 'retry'; childKey: string; attemptId: string },
    ) =>
      request.operation === 'retry'
        ? service.retry(selectedId, request.childKey, request.attemptId)
        : service[request.operation](selectedId),
    onSuccess: update,
  });
  const deliveryWorkflows =
    catalog.data?.filter(
      (workflow) => workflow.graph?.executionContract === 'developer-delivery/v1',
    ) ?? [];
  const error =
    launch.error ??
    action.error ??
    runs.error ??
    selected.error ??
    catalog.error ??
    evidence.error ??
    deliveryWaits.error;
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
          launch.mutate({
            workflowId,
            prompt,
            repo,
            baseBranch,
            ...(model ? { model } : {}),
            ...(connectionId ? { connectionId } : {}),
          });
        }}
      >
        <label>
          Workflow
          <select
            required
            className={inputClass}
            value={workflowId}
            onChange={(event) => changeLaunchField(() => setWorkflowId(event.target.value))}
          >
            <option value="">Choose a developer workflow</option>
            {deliveryWorkflows.map((workflow) => (
              <option key={workflow.id} value={workflow.id}>
                {workflow.name}
              </option>
            ))}
          </select>
        </label>
        {catalog.isPending ? (
          <p role="status">Loading workflows…</p>
        ) : !catalog.isError && deliveryWorkflows.length === 0 ? (
          <p>No developer workflows are installed.</p>
        ) : null}
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
        <button type="submit" className={buttonClass} disabled={launch.isPending || !workflowId}>
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
                onClick={() => {
                  setSelectedId(run.executionId);
                  setShowEvidence(false);
                  setShowGraph(false);
                  focusEvidence.current = false;
                }}
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
          {showEvidence &&
            (evidence.isPending || deliveryWaits.isPending ? (
              <p role="status">Loading results…</p>
            ) : (
              evidence.data &&
              deliveryWaits.data && (
                <div ref={evidenceRegion} tabIndex={-1} className="niuu:space-y-3">
                  <WorkflowResults
                    ariaLabel="Execution evidence"
                    headingLevel={3}
                    title="Workflow results"
                    status={selected.data.state}
                    filename={`${selected.data.executionId}-results.md`}
                    context={[
                      { label: 'Generation', value: String(selected.data.currentGeneration) },
                      { label: 'Plan revision', value: selected.data.planRevision ?? 'Pending' },
                      { label: 'Recorded attempts', value: String(selected.data.children.length) },
                      { label: 'Updated', value: selected.data.updatedAt || 'Not reported' },
                    ]}
                    markdown={buildWorkflowExecutionResultsMarkdown(
                      selected.data,
                      evidence.data,
                      deliveryWaits.data,
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
                          execution: selected.data,
                          evidence: evidence.data,
                          deliveryWaits: deliveryWaits.data,
                        },
                        null,
                        2,
                      )}
                    </pre>
                  </details>
                </div>
              )
            ))}
        </section>
      )}
    </main>
  );
}
