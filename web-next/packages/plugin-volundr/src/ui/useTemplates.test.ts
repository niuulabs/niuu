import { describe, it, expect, vi } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createElement } from 'react';
import type { ReactNode } from 'react';
import {
  useTemplates,
  useUpdateTemplate,
  useCreateTemplate,
  useDeleteTemplate,
} from './useTemplates';
import type { Template } from '../domain/template';
import type { PodSpec } from '../domain/pod';

const SAMPLE_SPEC: PodSpec = {
  image: 'ghcr.io/niuulabs/skuld',
  tag: 'latest',
  mounts: [],
  env: {},
  envSecretRefs: [],
  tools: [],
  mcpServers: [],
  resources: {
    cpuRequest: '1',
    cpuLimit: '2',
    memRequestMi: 512,
    memLimitMi: 1024,
    gpuCount: 0,
  },
  ttlSec: 3600,
  idleTimeoutSec: 600,
};

const SAMPLE_TEMPLATE: Template = {
  id: 'tpl-1',
  name: 'default',
  version: 1,
  spec: SAMPLE_SPEC,
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
};

function makeWrapper(store: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(
      QueryClientProvider,
      { client },
      createElement(ServicesProvider, { services: { 'volundr.templates': store } }, children),
    );
  };
}

describe('useTemplates', () => {
  it('returns templates from the store', async () => {
    const store = { listTemplates: vi.fn().mockResolvedValue([SAMPLE_TEMPLATE]) };
    const { result } = renderHook(() => useTemplates(), { wrapper: makeWrapper(store) });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(1);
    expect(result.current.data?.[0]?.name).toBe('default');
    expect(store.listTemplates).toHaveBeenCalled();
  });

  it('starts in loading state', () => {
    const store = { listTemplates: vi.fn().mockReturnValue(new Promise(() => undefined)) };
    const { result } = renderHook(() => useTemplates(), { wrapper: makeWrapper(store) });
    expect(result.current.isLoading).toBe(true);
  });

  it('enters error state when store rejects', async () => {
    const store = { listTemplates: vi.fn().mockRejectedValue(new Error('store down')) };
    const { result } = renderHook(() => useTemplates(), { wrapper: makeWrapper(store) });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(Error);
  });
});

describe('useUpdateTemplate', () => {
  it('calls updateTemplate and invalidates queries on success', async () => {
    const updatedTemplate = { ...SAMPLE_TEMPLATE, version: 2 };
    const store = {
      listTemplates: vi.fn().mockResolvedValue([SAMPLE_TEMPLATE]),
      updateTemplate: vi.fn().mockResolvedValue(updatedTemplate),
    };
    const { result } = renderHook(() => useUpdateTemplate(), { wrapper: makeWrapper(store) });

    await act(async () => {
      await result.current.mutateAsync({ id: 'tpl-1', spec: SAMPLE_SPEC });
    });

    expect(store.updateTemplate).toHaveBeenCalledWith('tpl-1', SAMPLE_SPEC);
  });
});

describe('useCreateTemplate', () => {
  it('calls createTemplate and invalidates queries on success', async () => {
    const store = {
      listTemplates: vi.fn().mockResolvedValue([]),
      createTemplate: vi.fn().mockResolvedValue(SAMPLE_TEMPLATE),
    };
    const { result } = renderHook(() => useCreateTemplate(), { wrapper: makeWrapper(store) });

    await act(async () => {
      await result.current.mutateAsync({ name: 'new-tpl', spec: SAMPLE_SPEC });
    });

    expect(store.createTemplate).toHaveBeenCalledWith('new-tpl', SAMPLE_SPEC);
  });
});

describe('useDeleteTemplate', () => {
  it('calls deleteTemplate and invalidates queries on success', async () => {
    const store = {
      listTemplates: vi.fn().mockResolvedValue([SAMPLE_TEMPLATE]),
      deleteTemplate: vi.fn().mockResolvedValue(undefined),
    };
    const { result } = renderHook(() => useDeleteTemplate(), { wrapper: makeWrapper(store) });

    await act(async () => {
      await result.current.mutateAsync('tpl-1');
    });

    expect(store.deleteTemplate).toHaveBeenCalledWith('tpl-1');
  });
});
