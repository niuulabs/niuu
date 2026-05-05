import { describe, it, expect, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createElement, type ReactNode } from 'react';
import { useRavens, useRaven } from './useRavens';
import type { Ravn } from '../../domain/ravn';

const SAMPLE_RAVN: Ravn = {
  id: 'a3f1b2c4-8e7d-4a6f-9b0c-1d2e3f4a5b6c',
  personaName: 'coder',
  status: 'active',
  model: 'claude-sonnet-4-6',
  createdAt: '2026-04-15T09:00:00Z',
};

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

describe('useRavens', () => {
  it('returns ravens list from service', async () => {
    const svc = { listRavens: vi.fn().mockResolvedValue([SAMPLE_RAVN]) };
    const { result } = renderHook(() => useRavens(), {
      wrapper: makeWrapper({ 'ravn.ravens': svc }),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(1);
    expect(result.current.data![0]!.personaName).toBe('coder');
    expect(svc.listRavens).toHaveBeenCalled();
  });

  it('starts in loading state', () => {
    const svc = { listRavens: vi.fn().mockReturnValue(new Promise(() => undefined)) };
    const { result } = renderHook(() => useRavens(), {
      wrapper: makeWrapper({ 'ravn.ravens': svc }),
    });

    expect(result.current.isLoading).toBe(true);
  });

  it('enters error state when service rejects', async () => {
    const svc = {
      listRavens: vi.fn().mockRejectedValue(new Error('network error')),
    };
    const { result } = renderHook(() => useRavens(), {
      wrapper: makeWrapper({ 'ravn.ravens': svc }),
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(Error);
  });
});

describe('useRaven', () => {
  it('fetches a single ravn by id', async () => {
    const svc = { getRaven: vi.fn().mockResolvedValue(SAMPLE_RAVN) };
    const { result } = renderHook(() => useRaven('a3f1b2c4-8e7d-4a6f-9b0c-1d2e3f4a5b6c'), {
      wrapper: makeWrapper({ 'ravn.ravens': svc }),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.personaName).toBe('coder');
    expect(svc.getRaven).toHaveBeenCalledWith('a3f1b2c4-8e7d-4a6f-9b0c-1d2e3f4a5b6c');
  });

  it('is disabled when id is empty', () => {
    const svc = { getRaven: vi.fn() };
    const { result } = renderHook(() => useRaven(''), {
      wrapper: makeWrapper({ 'ravn.ravens': svc }),
    });

    expect(result.current.fetchStatus).toBe('idle');
    expect(svc.getRaven).not.toHaveBeenCalled();
  });
});
