import type { ReactNode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useEntities } from './useEntities';
import {
  useCreateRegistryMount,
  useDeleteRegistryMount,
  useRegistryMounts,
  useUpdateRegistryMount,
} from './useRegistryMounts';
import { useRouting } from './useRouting';
import type { RegistryMount } from '../domain/registry';

const mocks = vi.hoisted(() => ({ service: {} as Record<string, unknown> }));

vi.mock('@niuulabs/plugin-sdk', () => ({ useService: () => mocks.service }));

function renderMimirHook<T>(callback: () => T) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const rendered = renderHook(callback, {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
  return { ...rendered, queryClient };
}

const mount: Omit<RegistryMount, 'id'> = {
  name: 'shared',
  kind: 'remote',
  lifecycle: 'registered',
  role: 'shared',
  url: 'https://mimir.example.test',
  path: '',
  categories: ['research'],
  defaultReadPriority: 10,
  enabled: true,
  healthStatus: 'healthy',
  healthMessage: '',
  desc: 'Shared research mount',
};

describe('Mimir application hooks', () => {
  beforeEach(() => {
    mocks.service = { mounts: {}, pages: {} };
  });

  it('loads and groups unfiltered and filtered entities', async () => {
    const entities = [
      {
        path: 'people/ada',
        title: 'Ada',
        entityKind: 'person' as const,
        summary: 'Researcher',
        relationshipCount: 2,
      },
      {
        path: 'projects/niuu',
        title: 'Niuu',
        entityKind: 'project' as const,
        summary: 'Platform',
        relationshipCount: 4,
      },
    ];
    const listEntities = vi.fn(async (options?: { kind?: string }) =>
      options?.kind ? entities.filter((entity) => entity.entityKind === options.kind) : entities,
    );
    mocks.service = { pages: { listEntities }, mounts: {} };

    const { result } = renderMimirHook(() => useEntities());
    expect(result.current.entities).toEqual([]);
    await waitFor(() => expect(result.current.entities).toHaveLength(2));
    expect(result.current.grouped.person).toHaveLength(1);
    expect(result.current.grouped.project).toHaveLength(1);
    expect(result.current.grouped.org).toEqual([]);

    const filtered = renderMimirHook(() => useEntities('person'));
    await waitFor(() => expect(filtered.result.current.entities).toEqual([entities[0]]));
    expect(listEntities).toHaveBeenLastCalledWith({ kind: 'person' });
  });

  it('resolves routing paths and invalidates after mutations', async () => {
    const rule = {
      id: 'route-1',
      prefix: 'research/',
      mountName: 'shared',
      priority: 1,
      active: true,
    };
    const listRoutingRules = vi.fn(async () => [rule]);
    const upsertRoutingRule = vi.fn(async () => rule);
    const deleteRoutingRule = vi.fn(async () => undefined);
    mocks.service = {
      pages: {},
      mounts: { listRoutingRules, upsertRoutingRule, deleteRoutingRule },
    };

    const { result, queryClient } = renderMimirHook(() => useRouting());
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    expect(result.current.testResult).toBeNull();
    await waitFor(() => expect(result.current.rules).toEqual([rule]));
    act(() => result.current.setTestPath('  research/topic  '));
    expect(result.current.testResult).toMatchObject({
      path: 'research/topic',
      mountName: 'shared',
    });
    act(() => result.current.upsertRule(rule));
    act(() => result.current.deleteRule(rule.id));
    await waitFor(() => expect(upsertRoutingRule).toHaveBeenCalledWith(rule));
    await waitFor(() => expect(deleteRoutingRule).toHaveBeenCalledWith(rule.id));
    await waitFor(() => expect(invalidate).toHaveBeenCalled());
  });

  it('returns empty registry mounts and rejects unavailable mutations', async () => {
    const mounts = renderMimirHook(() => useRegistryMounts());
    await waitFor(() => expect(mounts.result.current.data).toEqual([]));
    const create = renderMimirHook(() => useCreateRegistryMount());
    const update = renderMimirHook(() => useUpdateRegistryMount());
    const remove = renderMimirHook(() => useDeleteRegistryMount());
    await expect(create.result.current.mutateAsync(mount)).rejects.toThrow(/not available/i);
    await expect(update.result.current.mutateAsync({ id: 'mount-1', mount })).rejects.toThrow(
      /not available/i,
    );
    await expect(remove.result.current.mutateAsync('mount-1')).rejects.toThrow(/not available/i);
  });

  it('calls registry methods and invalidates mount queries after success', async () => {
    const saved = { ...mount, id: 'mount-1' };
    const listRegistryMounts = vi.fn(async () => [saved]);
    const createRegistryMount = vi.fn(async () => saved);
    const updateRegistryMount = vi.fn(async () => saved);
    const deleteRegistryMount = vi.fn(async () => undefined);
    mocks.service = {
      pages: {},
      mounts: {
        listRegistryMounts,
        createRegistryMount,
        updateRegistryMount,
        deleteRegistryMount,
      },
    };

    const mounts = renderMimirHook(() => useRegistryMounts());
    await waitFor(() => expect(mounts.result.current.data).toEqual([saved]));
    expect(listRegistryMounts).toHaveBeenCalledOnce();

    const create = renderMimirHook(() => useCreateRegistryMount());
    const createInvalidate = vi.spyOn(create.queryClient, 'invalidateQueries');
    await act(async () => {
      await create.result.current.mutateAsync(mount);
    });
    expect(createRegistryMount).toHaveBeenCalledWith(mount);
    expect(createInvalidate).toHaveBeenCalledTimes(2);

    const update = renderMimirHook(() => useUpdateRegistryMount());
    const updateInvalidate = vi.spyOn(update.queryClient, 'invalidateQueries');
    await act(async () => {
      await update.result.current.mutateAsync({ id: saved.id, mount });
    });
    expect(updateRegistryMount).toHaveBeenCalledWith(saved.id, mount);
    expect(updateInvalidate).toHaveBeenCalledTimes(2);

    const remove = renderMimirHook(() => useDeleteRegistryMount());
    const deleteInvalidate = vi.spyOn(remove.queryClient, 'invalidateQueries');
    await act(async () => {
      await remove.result.current.mutateAsync(saved.id);
    });
    expect(deleteRegistryMount).toHaveBeenCalledWith(saved.id);
    expect(deleteInvalidate).toHaveBeenCalledTimes(2);
  });
});
