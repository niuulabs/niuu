import { describe, it, expect, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createElement, type ReactNode } from 'react';
import { useFleetBudget, useRavnBudget, useRavnBudgets } from './useBudget';
import type { BudgetState } from '@niuulabs/domain';

const BUDGET: BudgetState = { spentUsd: 3.61, capUsd: 5.0, warnAt: 0.7 };
const FLEET_BUDGET: BudgetState = { spentUsd: 10.34, capUsd: 20.7, warnAt: 0.7 };

function makeWrapper(services: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(
      QueryClientProvider,
      { client },
      createElement(ServicesProvider, { services }, children),
    );
  };
}

describe('useFleetBudget', () => {
  it('returns fleet budget', async () => {
    const svc = { getFleetBudget: vi.fn().mockResolvedValue(FLEET_BUDGET), getBudget: vi.fn() };
    const { result } = renderHook(() => useFleetBudget(), {
      wrapper: makeWrapper({ 'ravn.budget': svc }),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.spentUsd).toBe(10.34);
  });

  it('enters error state when service rejects', async () => {
    const svc = {
      getFleetBudget: vi.fn().mockRejectedValue(new Error('budget unavailable')),
      getBudget: vi.fn(),
    };
    const { result } = renderHook(() => useFleetBudget(), {
      wrapper: makeWrapper({ 'ravn.budget': svc }),
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

describe('useRavnBudget', () => {
  it('returns budget for a specific ravn', async () => {
    const svc = { getBudget: vi.fn().mockResolvedValue(BUDGET), getFleetBudget: vi.fn() };
    const { result } = renderHook(() => useRavnBudget('ravn-id-1'), {
      wrapper: makeWrapper({ 'ravn.budget': svc }),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.spentUsd).toBe(3.61);
    expect(svc.getBudget).toHaveBeenCalledWith('ravn-id-1');
  });

  it('is disabled when ravnId is empty', () => {
    const svc = { getBudget: vi.fn(), getFleetBudget: vi.fn() };
    const { result } = renderHook(() => useRavnBudget(''), {
      wrapper: makeWrapper({ 'ravn.budget': svc }),
    });

    expect(result.current.fetchStatus).toBe('idle');
    expect(svc.getBudget).not.toHaveBeenCalled();
  });
});

describe('useRavnBudgets', () => {
  it('returns a record of ravnId → budget', async () => {
    const svc = {
      getBudget: vi.fn().mockResolvedValue(BUDGET),
      getFleetBudget: vi.fn(),
    };
    const { result } = renderHook(() => useRavnBudgets(['id-1', 'id-2']), {
      wrapper: makeWrapper({ 'ravn.budget': svc }),
    });

    await waitFor(() => expect(Object.keys(result.current).length).toBe(2));
    expect(result.current['id-1']?.spentUsd).toBe(3.61);
    expect(result.current['id-2']?.spentUsd).toBe(3.61);
  });

  it('returns empty record for empty ids list', async () => {
    const svc = { getBudget: vi.fn(), getFleetBudget: vi.fn() };
    const { result } = renderHook(() => useRavnBudgets([]), {
      wrapper: makeWrapper({ 'ravn.budget': svc }),
    });

    expect(result.current).toEqual({});
    expect(svc.getBudget).not.toHaveBeenCalled();
  });
});
