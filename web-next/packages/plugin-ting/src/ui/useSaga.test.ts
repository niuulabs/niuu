import { describe, it, expect, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createElement } from 'react';
import type { ReactNode } from 'react';
import { useAssignSagaRepos, useAssignSagaTarget, useAssignSagaWorkflow, useSaga } from './useSaga';
import type { Saga } from '../domain/saga';

const MOCK_SAGA: Saga = {
  id: '00000000-0000-0000-0000-000000000001',
  trackerId: 'NIU-500',
  trackerType: 'linear',
  slug: 'auth-rewrite',
  name: 'Auth Rewrite',
  repos: ['niuulabs/volundr'],
  featureBranch: 'feat/auth-rewrite',
  status: 'active',
  confidence: 82,
  createdAt: '2026-01-10T09:00:00Z',
  phaseSummary: { total: 3, completed: 1 },
};
const OTHER_SAGA: Saga = {
  ...MOCK_SAGA,
  id: '00000000-0000-0000-0000-000000000002',
  trackerId: 'NIU-501',
  slug: 'other-saga',
  name: 'Other Saga',
};

function makeWrapper(
  service: Record<string, unknown>,
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(
      QueryClientProvider,
      { client },
      createElement(ServicesProvider, { services: service }, children),
    );
  };
}

describe('useSaga', () => {
  it('returns saga data from the service', async () => {
    const svc = { getSaga: vi.fn().mockResolvedValue(MOCK_SAGA) };
    const { result } = renderHook(() => useSaga(MOCK_SAGA.id), {
      wrapper: makeWrapper({ ting: svc }),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(MOCK_SAGA);
    expect(svc.getSaga).toHaveBeenCalledWith(MOCK_SAGA.id);
  });

  it('returns null when saga is not found', async () => {
    const svc = { getSaga: vi.fn().mockResolvedValue(null) };
    const { result } = renderHook(() => useSaga('nonexistent'), {
      wrapper: makeWrapper({ ting: svc }),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
  });

  it('enters error state when service rejects', async () => {
    const svc = { getSaga: vi.fn().mockRejectedValue(new Error('saga unavailable')) };
    const { result } = renderHook(() => useSaga(MOCK_SAGA.id), {
      wrapper: makeWrapper({ ting: svc }),
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(Error);
  });

  it('starts in loading state', () => {
    const svc = { getSaga: vi.fn().mockReturnValue(new Promise(() => undefined)) };
    const { result } = renderHook(() => useSaga(MOCK_SAGA.id), {
      wrapper: makeWrapper({ ting: svc }),
    });
    expect(result.current.isLoading).toBe(true);
  });

  it('is disabled when id is empty string', () => {
    const svc = { getSaga: vi.fn() };
    const { result } = renderHook(() => useSaga(''), {
      wrapper: makeWrapper({ ting: svc }),
    });
    expect(result.current.fetchStatus).toBe('idle');
    expect(svc.getSaga).not.toHaveBeenCalled();
  });

  it('assigns repositories and updates cached saga data', async () => {
    const updatedSaga: Saga = {
      ...MOCK_SAGA,
      repos: ['niuulabs/volundr', 'niuulabs/infrastructure'],
      repoRefs: [
        { repo: 'niuulabs/volundr', branch: 'dev' },
        { repo: 'niuulabs/infrastructure', branch: 'main' },
      ],
    };
    const svc = { assignRepos: vi.fn().mockResolvedValue(updatedSaga) };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(['ting', 'sagas', MOCK_SAGA.id], MOCK_SAGA);
    client.setQueryData(['ting', 'sagas'], [OTHER_SAGA, MOCK_SAGA]);

    const { result } = renderHook(() => useAssignSagaRepos(MOCK_SAGA.id), {
      wrapper: makeWrapper({ ting: svc }, client),
    });

    await result.current.mutateAsync(updatedSaga.repoRefs ?? []);

    expect(svc.assignRepos).toHaveBeenCalledWith(MOCK_SAGA.id, updatedSaga.repoRefs);
    expect(client.getQueryData(['ting', 'sagas', MOCK_SAGA.id])).toEqual(updatedSaga);
    expect(client.getQueryData(['ting', 'sagas'])).toEqual([OTHER_SAGA, updatedSaga]);
  });

  it('assigns workflow and updates cached saga data', async () => {
    const updatedSaga: Saga = { ...MOCK_SAGA, workflowId: 'workflow-1', workflow: 'Ship' };
    const svc = { assignWorkflow: vi.fn().mockResolvedValue(updatedSaga) };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(['ting', 'sagas'], [OTHER_SAGA, MOCK_SAGA]);

    const { result } = renderHook(() => useAssignSagaWorkflow(MOCK_SAGA.id), {
      wrapper: makeWrapper({ ting: svc }, client),
    });

    await result.current.mutateAsync('workflow-1');

    expect(svc.assignWorkflow).toHaveBeenCalledWith(MOCK_SAGA.id, 'workflow-1');
    expect(client.getQueryData(['ting', 'sagas', MOCK_SAGA.id])).toEqual(updatedSaga);
    expect(client.getQueryData(['ting', 'sagas'])).toEqual([OTHER_SAGA, updatedSaga]);
  });

  it('assigns target and updates cached saga data', async () => {
    const target = { mode: 'cluster' as const, cluster: 'ymir', targetTags: ['k8s'] };
    const updatedSaga: Saga = { ...MOCK_SAGA, targetTags: ['k8s'] };
    const svc = { assignTarget: vi.fn().mockResolvedValue(updatedSaga) };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(['ting', 'sagas'], [OTHER_SAGA, MOCK_SAGA]);

    const { result } = renderHook(() => useAssignSagaTarget(MOCK_SAGA.id), {
      wrapper: makeWrapper({ ting: svc }, client),
    });

    await result.current.mutateAsync(target);

    expect(svc.assignTarget).toHaveBeenCalledWith(MOCK_SAGA.id, target);
    expect(client.getQueryData(['ting', 'sagas', MOCK_SAGA.id])).toEqual(updatedSaga);
    expect(client.getQueryData(['ting', 'sagas'])).toEqual([OTHER_SAGA, updatedSaga]);
  });

  it('leaves non-list saga cache entries alone for every assignment', async () => {
    const updatedSaga: Saga = { ...MOCK_SAGA, workflowId: 'workflow-1' };
    const svc = {
      assignWorkflow: vi.fn().mockResolvedValue(updatedSaga),
      assignTarget: vi.fn().mockResolvedValue(updatedSaga),
      assignRepos: vi.fn().mockResolvedValue(updatedSaga),
    };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(['ting', 'sagas'], { stale: true });

    const { result } = renderHook(
      () => ({
        workflow: useAssignSagaWorkflow(MOCK_SAGA.id),
        target: useAssignSagaTarget(MOCK_SAGA.id),
        repos: useAssignSagaRepos(MOCK_SAGA.id),
      }),
      { wrapper: makeWrapper({ ting: svc }, client) },
    );

    await result.current.workflow.mutateAsync('workflow-1');
    await result.current.target.mutateAsync({ mode: 'cluster', cluster: 'ymir', targetTags: [] });
    await result.current.repos.mutateAsync([{ repo: 'niuulabs/niuu', branch: 'dev' }]);

    expect(client.getQueryData(['ting', 'sagas'])).toEqual({ stale: true });
  });
});
