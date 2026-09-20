import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { describe, expect, it, vi } from 'vitest';
import { DeveloperExecutionGraph } from './DeveloperExecutionGraph';
import type { DeveloperExecution } from '../domain/developerExecution';
import { traceFixture } from '../application/developerExecutionTrace.fixture';

const execution: DeveloperExecution = {
  executionId: 'run',
  name: 'Ticket',
  prompt: 'Fix',
  repo: 'repo',
  workflowId: 'workflow',
  state: 'completed',
  suspensionReason: '',
  currentGeneration: 2,
  children: [],
  join: null,
  budget: { totalUnits: 10, spentUnits: 1, reservedUnits: 0, availableUnits: 9 },
  createdAt: '',
  updatedAt: 'finished',
};
function setup(trace = vi.fn().mockResolvedValue(traceFixture)) {
  const onOpenEvidence = vi.fn();
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ServicesProvider services={{ 'ting.developerExecutions': { trace } }}>
        <DeveloperExecutionGraph execution={execution} onOpenEvidence={onOpenEvidence} />
      </ServicesProvider>
    </QueryClientProvider>,
  );
  return { trace, onOpenEvidence };
}

describe('DeveloperExecutionGraph', () => {
  it('loads parent history, drills into an exact child attempt, and links evidence', async () => {
    const { trace, onOpenEvidence } = setup();
    await screen.findByLabelText('Workflow execution');
    expect(trace).toHaveBeenCalledWith('run', { after: 0 });
    fireEvent.change(screen.getByLabelText('Workflow execution'), { target: { value: 'c1' } });
    await waitFor(() => expect(trace).toHaveBeenCalledWith('run', { childId: 'c1', after: 0 }));
    fireEvent.click(screen.getAllByRole('button', { name: 'Open run evidence' })[0]!);
    expect(onOpenEvidence).toHaveBeenCalledOnce();
  });
  it('shows loading and a retryable error without inventing an empty successful graph', async () => {
    let reject!: (reason: Error) => void;
    const trace = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise((_, fail) => {
            reject = fail;
          }),
      )
      .mockResolvedValue(traceFixture);
    setup(trace);
    expect(screen.getByRole('status')).toHaveTextContent('Loading execution graph');
    reject(new Error('History unavailable'));
    expect(await screen.findByRole('alert')).toHaveTextContent('History unavailable');
    fireEvent.click(screen.getByRole('button', { name: 'Retry graph' }));
    await screen.findByLabelText('Workflow execution');
  });
  it('uses the raw cursor for additional history and exposes unattached outcomes', async () => {
    const trace = vi
      .fn()
      .mockResolvedValueOnce({
        ...traceFixture,
        page: { after: 0, scannedThrough: 100, hasMore: true },
      })
      .mockResolvedValueOnce({
        ...traceFixture,
        events: [{ ...traceFixture.events[0], eventId: 'unattached', nodeIds: [] }],
        page: { after: 100, scannedThrough: 101, hasMore: false },
      });
    setup(trace);
    fireEvent.click(await screen.findByRole('button', { name: 'Load more output' }));
    await waitFor(() => expect(trace).toHaveBeenLastCalledWith('run', { after: 100 }));
    expect(await screen.findByText('Run activity without a unique stage (1)')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Load more output' })).not.toBeInTheDocument();
  });
});
