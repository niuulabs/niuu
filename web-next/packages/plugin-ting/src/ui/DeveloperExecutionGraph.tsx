import { useRef, useState } from 'react';
import { keepPreviousData, useInfiniteQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IDeveloperExecutionService } from '../ports';
import type { DeveloperExecution } from '../domain/developerExecution';
import { executionIsTerminal } from '../domain/developerExecution';
import { projectDeveloperExecutionTrace } from '../application/developerExecutionTrace';
import { WorkflowExecutionGraph } from './WorkflowExecutionGraph';

const buttonClass =
  'niuu:rounded niuu:border niuu:border-border niuu:px-3 niuu:py-2 niuu:disabled:opacity-50';

/** Fetch only the selected parent/child session through the execution service. */
export function DeveloperExecutionGraph({
  execution,
  onOpenEvidence,
}: {
  execution: DeveloperExecution;
  onOpenEvidence: () => void;
}) {
  const service = useService<IDeveloperExecutionService>('ting.developerExecutions');
  const [childId, setChildId] = useState('');
  const sessionSelect = useRef<HTMLSelectElement>(null);
  const terminal = executionIsTerminal(execution.state);
  const trace = useInfiniteQuery({
    queryKey: [
      'ting',
      'developer-trace',
      execution.executionId,
      childId,
      terminal ? `terminal:${execution.updatedAt}` : 'active',
    ],
    initialPageParam: 0,
    placeholderData: keepPreviousData,
    queryFn: ({ pageParam }) =>
      service.trace(execution.executionId, {
        ...(childId ? { childId } : {}),
        after: pageParam,
      }),
    getNextPageParam: (page) =>
      page.page.hasMore && page.page.scannedThrough > page.page.after
        ? page.page.scannedThrough
        : undefined,
    refetchInterval: terminal ? false : 3000,
  });
  const pages = trace.data?.pages;
  const latest = pages?.at(-1);
  const graph = pages ? projectDeveloperExecutionTrace(pages) : null;
  const sessions = latest?.sessions ?? [];
  const selectedSession = sessions.find((session) => session.childId === latest?.selectedChildId);

  return (
    <section aria-label="Run execution graph" className="niuu:space-y-3">
      <div className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-3">
        <h3 className="niuu:text-lg niuu:font-semibold">Execution graph</h3>
        <span className="niuu:text-sm niuu:text-text-secondary">
          {terminal ? 'Recorded run' : 'Following run'} · {execution.state}
        </span>
        <button className={buttonClass} onClick={onOpenEvidence}>
          Open run evidence
        </button>
        <button
          className={buttonClass}
          disabled={trace.isFetching}
          onClick={() => void trace.refetch()}
        >
          Refresh graph
        </button>
      </div>
      {(trace.isPending || trace.isPlaceholderData) && (
        <p role="status">Loading execution graph…</p>
      )}
      {trace.error && (
        <div role="alert" className="niuu:space-y-2 niuu:text-critical">
          <p>Execution graph could not be refreshed: {trace.error.message}</p>
          <button className={buttonClass} onClick={() => void trace.refetch()}>
            Retry graph
          </button>
        </div>
      )}
      {latest && graph && (
        <>
          <label className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-3">
            Workflow execution
            <select
              ref={sessionSelect}
              className="niuu:max-w-full niuu:rounded niuu:border niuu:border-border niuu:bg-bg-secondary niuu:p-2"
              value={childId}
              onChange={(event) => setChildId(event.target.value)}
            >
              {sessions.map((session) => (
                <option key={session.childId ?? 'parent'} value={session.childId ?? ''}>
                  {session.kind === 'parent'
                    ? 'Coordinator workflow'
                    : `${session.childKey} · generation ${session.generation} · attempt ${session.attempt} · ${session.state}${session.generation !== execution.currentGeneration ? ' · historical' : ''}${!session.taskId ? ' · no task yet' : ''}`}
                </option>
              ))}
            </select>
          </label>
          {!trace.isPlaceholderData && (
            <>
              <p className="niuu:text-sm niuu:text-text-secondary">
                Frozen workflow · {latest.workflow.name}. Public outcomes are shown in sequence
                order. “Output recorded” confirms an outcome exists; it does not assert stage
                completion.
                {trace.hasNextPage &&
                  ' More history is available below; this graph shows the loaded portion.'}
              </p>
              {selectedSession?.kind === 'child' && (
                <p className="niuu:text-sm niuu:text-text-secondary">
                  Workstream {selectedSession.childKey} · generation {selectedSession.generation} ·
                  attempt {selectedSession.attempt}
                  {' · '}
                  {selectedSession.state}
                </p>
              )}
              <WorkflowExecutionGraph
                key={latest.selectedChildId ?? 'parent'}
                title={latest.workflow.name}
                nodes={graph.nodes}
                edges={graph.edges}
                onOpenChildWorkflow={(child) => {
                  sessionSelect.current?.focus();
                  setChildId(child.id);
                }}
                onOpenEvidence={onOpenEvidence}
              />
              {graph.unattached.length > 0 && (
                <details className="niuu:rounded niuu:border niuu:border-border niuu:p-3">
                  <summary className="niuu:cursor-pointer">
                    Run activity without a unique stage ({graph.unattached.length})
                  </summary>
                  <p className="niuu:my-2 niuu:text-sm niuu:text-text-secondary">
                    These recorded outcomes cannot be assigned to one frozen graph node.
                  </p>
                  {graph.unattached.map((event) => (
                    <details
                      key={event.eventId}
                      className="niuu:border-t niuu:border-border niuu:py-2"
                    >
                      <summary>
                        #{event.seq} · {event.persona} · {event.eventType} · {event.verdict}
                      </summary>
                      <p>{event.summary}</p>
                      <pre className="niuu:max-h-80 niuu:overflow-auto niuu:whitespace-pre-wrap niuu:break-words niuu:text-xs">
                        {JSON.stringify(event.fields, null, 2)}
                      </pre>
                    </details>
                  ))}
                </details>
              )}
              {trace.hasNextPage && (
                <button
                  className={buttonClass}
                  disabled={trace.isFetching}
                  onClick={() => void trace.fetchNextPage()}
                >
                  {trace.isFetchingNextPage ? 'Loading more output…' : 'Load more output'}
                </button>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
