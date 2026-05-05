import { describe, it, expect, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createElement, type ReactNode } from 'react';
import { useTriggers } from './useTriggers';
import type { Trigger } from '../../domain/trigger';

const SAMPLE_TRIGGER: Trigger = {
  id: 'aa000001-0000-4000-8000-000000000001',
  kind: 'cron',
  personaName: 'eir',
  spec: '0 * * * *',
  enabled: true,
  createdAt: '2026-04-01T00:00:00Z',
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

describe('useTriggers', () => {
  it('returns triggers list from service', async () => {
    const svc = { listTriggers: vi.fn().mockResolvedValue([SAMPLE_TRIGGER]) };
    const { result } = renderHook(() => useTriggers(), {
      wrapper: makeWrapper({ 'ravn.triggers': svc }),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(1);
    expect(result.current.data![0]!.personaName).toBe('eir');
    expect(svc.listTriggers).toHaveBeenCalled();
  });

  it('starts in loading state', () => {
    const svc = { listTriggers: vi.fn().mockReturnValue(new Promise(() => undefined)) };
    const { result } = renderHook(() => useTriggers(), {
      wrapper: makeWrapper({ 'ravn.triggers': svc }),
    });

    expect(result.current.isLoading).toBe(true);
  });

  it('enters error state when service rejects', async () => {
    const svc = {
      listTriggers: vi.fn().mockRejectedValue(new Error('service unavailable')),
    };
    const { result } = renderHook(() => useTriggers(), {
      wrapper: makeWrapper({ 'ravn.triggers': svc }),
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
