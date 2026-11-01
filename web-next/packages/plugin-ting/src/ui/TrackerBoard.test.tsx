import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { TrackerBoard } from './TrackerBoard';

vi.mock('./ProjectBoard', () => ({
  ProjectBoard: ({ projectId }: { projectId: string }) => <div>board for {projectId}</div>,
}));

function wrap(services: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={services}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

const saga = { id: 'saga-1', trackerId: 'tracker-1' };

function services(detail: unknown) {
  return {
    ting: { getSagas: async () => [saga] },
    'ting.work': { get: vi.fn(async () => detail) },
  };
}

describe('TrackerBoard', () => {
  it('draws the board of the Work project behind the tracker project', async () => {
    const work = services({ board: { workflows: [] }, tasks: [], coverage: [] });
    render(<TrackerBoard trackerId="tracker-1" fallback="none" />, { wrapper: wrap(work) });
    expect(await screen.findByText('board for saga-1')).toBeInTheDocument();
    expect(work['ting.work'].get).toHaveBeenCalledWith('project', 'saga-1');
  });

  it('retries a source that fell short', async () => {
    const work = services({
      board: { workflows: [] },
      tasks: [],
      coverage: [{ source: 'tracker', status: 'failed' }],
    });
    render(<TrackerBoard trackerId="tracker-1" fallback="none" />, { wrapper: wrap(work) });
    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(work['ting.work'].get).toHaveBeenCalledTimes(2));
  });

  it('shows the fallback for a tracker project Work does not know', async () => {
    render(<TrackerBoard trackerId="other" fallback="no board here" />, {
      wrapper: wrap(services(null)),
    });
    expect(await screen.findByText('no board here')).toBeInTheDocument();
  });

  it('shows the fallback when the project has no workflow with columns', async () => {
    render(<TrackerBoard trackerId="tracker-1" fallback="no columns" />, {
      wrapper: wrap(services({ board: null, tasks: [], coverage: [] })),
    });
    expect(await screen.findByText('no columns')).toBeInTheDocument();
  });

  it('reports a board that could not load', async () => {
    const failing = {
      ting: { getSagas: async () => [saga] },
      'ting.work': { get: async () => Promise.reject(new Error('work is down')) },
    };
    render(<TrackerBoard trackerId="tracker-1" fallback="none" />, { wrapper: wrap(failing) });
    expect(await screen.findByText('Could not load the board')).toBeInTheDocument();
  });
});
