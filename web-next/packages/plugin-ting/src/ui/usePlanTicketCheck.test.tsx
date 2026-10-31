import type { ReactNode } from 'react';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { describe, expect, it, vi } from 'vitest';
import type { ExtractedStructure } from '../ports';
import { usePlanTicketCheck } from './usePlanTicketCheck';

const structure = {
  found: true,
  structure: {
    name: 'Reliability',
    phases: [
      {
        name: 'Build',
        runs: [
          {
            name: 'Implement retry budget',
            description: 'Budget retries per run',
            acceptanceCriteria: ['Retries stop at the budget'],
            declaredFiles: [],
            estimateHours: 2,
            confidence: 80,
          },
        ],
      },
    ],
  },
} as ExtractedStructure;

function setup(listIssues: ReturnType<typeof vi.fn>) {
  const getSagas = vi.fn().mockResolvedValue([
    { trackerId: 'match', trackerConnectionId: 'linear', repos: ['niuulabs/niuu'] },
    { trackerId: 'other', trackerConnectionId: 'linear', repos: ['niuulabs/other'] },
  ]);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <ServicesProvider services={{ ting: { getSagas }, 'ting.tracker': { listIssues } }}>
        {children}
      </ServicesProvider>
    </QueryClientProvider>
  );
  return { getSagas, wrapper };
}

describe('usePlanTicketCheck', () => {
  it('only reads tickets from projects on the planned repository', async () => {
    const listIssues = vi
      .fn()
      .mockResolvedValue([{ identifier: 'PLT-207', title: 'Implement the retry budget' }]);
    const { wrapper } = setup(listIssues);

    const { result } = renderHook(() => usePlanTicketCheck('niuulabs/niuu', structure), {
      wrapper,
    });

    await waitFor(() => expect(result.current.flags).toHaveLength(1));
    expect(listIssues).toHaveBeenCalledTimes(1);
    expect(listIssues).toHaveBeenCalledWith('match', undefined, 'linear');
    expect(result.current.error).toBeNull();
  });

  it('finds imported projects when the plan names its repository by clone URL', async () => {
    const listIssues = vi.fn().mockResolvedValue([]);
    const { wrapper } = setup(listIssues);

    renderHook(() => usePlanTicketCheck('https://github.com/niuulabs/niuu.git', structure), {
      wrapper,
    });

    await waitFor(() => expect(listIssues).toHaveBeenCalledWith('match', undefined, 'linear'));
    expect(listIssues).toHaveBeenCalledTimes(1);
  });

  it('waits for a draft before reading anything', () => {
    const listIssues = vi.fn();
    const { getSagas, wrapper } = setup(listIssues);

    renderHook(() => usePlanTicketCheck('niuulabs/niuu', null), { wrapper });

    expect(getSagas).not.toHaveBeenCalled();
  });

  it('reports a failed lookup instead of implying there were no matches', async () => {
    const { wrapper } = setup(vi.fn().mockRejectedValue(new Error('Linear is offline')));

    const { result } = renderHook(() => usePlanTicketCheck('niuulabs/niuu', structure), {
      wrapper,
    });

    await waitFor(() =>
      expect(result.current.error).toBe(
        'Could not compare with existing tickets: Linear is offline',
      ),
    );
  });
});
