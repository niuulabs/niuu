import { describe, it, expect, vi } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createElement } from 'react';
import type { ReactNode } from 'react';
import {
  useWorkflows,
  useWorkflow,
  useWorkflowVersions,
  useLoadWorkflowVersion,
  useCreateWorkflow,
  useDeleteWorkflow,
  useExportWorkflow,
  useSaveWorkflow,
  usePreviewWorkflowImport,
  useApplyWorkflowImport,
  useLaunchWorkflow,
} from './useWorkflows';
import type { Workflow } from '../domain/workflow';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const wf1: Workflow = {
  id: '00000000-0000-0000-0000-000000000001',
  name: 'Workflow 1',
  nodes: [],
  edges: [],
};

const wf2: Workflow = {
  id: '00000000-0000-0000-0000-000000000002',
  name: 'Workflow 2',
  nodes: [],
  edges: [],
};

function makeWrapper(service: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(
      QueryClientProvider,
      { client },
      createElement(ServicesProvider, { services: service }, children),
    );
  };
}

// ---------------------------------------------------------------------------
// useWorkflows
// ---------------------------------------------------------------------------

describe('useWorkflows', () => {
  it('returns workflows list from the service', async () => {
    const svc = { listWorkflows: vi.fn().mockResolvedValue([wf1, wf2]) };
    const { result } = renderHook(() => useWorkflows(), {
      wrapper: makeWrapper({ 'ting.workflows': svc }),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(2);
    expect(result.current.data?.[0]?.name).toBe('Workflow 1');
    expect(svc.listWorkflows).toHaveBeenCalled();
  });

  it('enters error state when service rejects', async () => {
    const svc = { listWorkflows: vi.fn().mockRejectedValue(new Error('unavailable')) };
    const { result } = renderHook(() => useWorkflows(), {
      wrapper: makeWrapper({ 'ting.workflows': svc }),
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(Error);
  });

  it('starts in loading state', () => {
    const svc = { listWorkflows: vi.fn().mockReturnValue(new Promise(() => undefined)) };
    const { result } = renderHook(() => useWorkflows(), {
      wrapper: makeWrapper({ 'ting.workflows': svc }),
    });
    expect(result.current.isLoading).toBe(true);
  });

  it('returns empty array when service returns empty list', async () => {
    const svc = { listWorkflows: vi.fn().mockResolvedValue([]) };
    const { result } = renderHook(() => useWorkflows(), {
      wrapper: makeWrapper({ 'ting.workflows': svc }),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// useWorkflow
// ---------------------------------------------------------------------------

describe('useWorkflow', () => {
  it('returns a single workflow by id', async () => {
    const svc = {
      getWorkflow: vi.fn().mockResolvedValue(wf1),
    };
    const { result } = renderHook(() => useWorkflow(wf1.id), {
      wrapper: makeWrapper({ 'ting.workflows': svc }),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.name).toBe('Workflow 1');
    expect(svc.getWorkflow).toHaveBeenCalledWith(wf1.id);
  });

  it('returns null when workflow not found', async () => {
    const svc = { getWorkflow: vi.fn().mockResolvedValue(null) };
    const { result } = renderHook(() => useWorkflow('missing'), {
      wrapper: makeWrapper({ 'ting.workflows': svc }),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
  });

  it('enters error state when service rejects', async () => {
    const svc = { getWorkflow: vi.fn().mockRejectedValue(new Error('not found')) };
    const { result } = renderHook(() => useWorkflow('bad-id'), {
      wrapper: makeWrapper({ 'ting.workflows': svc }),
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

// ---------------------------------------------------------------------------
// useCreateWorkflow
// ---------------------------------------------------------------------------

describe('useCreateWorkflow', () => {
  it('calls saveWorkflow with a new blank workflow', async () => {
    const newWf: Workflow = { id: 'new-uuid', name: 'New Workflow', nodes: [], edges: [] };
    const svc = {
      listWorkflows: vi.fn().mockResolvedValue([wf1]),
      saveWorkflow: vi.fn().mockResolvedValue(newWf),
    };
    const { result } = renderHook(() => useCreateWorkflow(), {
      wrapper: makeWrapper({ 'ting.workflows': svc }),
    });
    await act(async () => {
      result.current.mutate(undefined);
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(svc.saveWorkflow).toHaveBeenCalledTimes(1);
    const arg = svc.saveWorkflow.mock.calls[0]![0] as Workflow;
    expect(arg.name).toBe('New Workflow');
    expect(arg.nodes).toHaveLength(0);
    expect(arg.edges).toHaveLength(0);
  });

  it('enters error state when saveWorkflow rejects', async () => {
    const svc = {
      listWorkflows: vi.fn().mockResolvedValue([]),
      saveWorkflow: vi.fn().mockRejectedValue(new Error('save failed')),
    };
    const { result } = renderHook(() => useCreateWorkflow(), {
      wrapper: makeWrapper({ 'ting.workflows': svc }),
    });
    await act(async () => {
      result.current.mutate(undefined);
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

// ---------------------------------------------------------------------------
// useDeleteWorkflow
// ---------------------------------------------------------------------------

describe('useDeleteWorkflow', () => {
  it('calls deleteWorkflow with the given id', async () => {
    const svc = {
      listWorkflows: vi.fn().mockResolvedValue([wf1]),
      deleteWorkflow: vi.fn().mockResolvedValue(undefined),
    };
    const { result } = renderHook(() => useDeleteWorkflow(), {
      wrapper: makeWrapper({ 'ting.workflows': svc }),
    });
    await act(async () => {
      result.current.mutate(wf1.id);
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(svc.deleteWorkflow).toHaveBeenCalledWith(wf1.id);
  });

  it('enters error state when deleteWorkflow rejects', async () => {
    const svc = {
      listWorkflows: vi.fn().mockResolvedValue([wf1]),
      deleteWorkflow: vi.fn().mockRejectedValue(new Error('delete failed')),
    };
    const { result } = renderHook(() => useDeleteWorkflow(), {
      wrapper: makeWrapper({ 'ting.workflows': svc }),
    });
    await act(async () => {
      result.current.mutate(wf1.id);
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

describe('useSaveWorkflow', () => {
  it('saves the workflow and returns what the service persisted', async () => {
    const saved = { ...wf1, name: 'Renamed', version: '1.1.0' };
    const svc = { saveWorkflow: vi.fn().mockResolvedValue(saved) };
    const { result } = renderHook(() => useSaveWorkflow(), {
      wrapper: makeWrapper({ 'ting.workflows': svc }),
    });

    let returned: Workflow | undefined;
    await act(async () => {
      returned = await result.current.mutateAsync(wf1);
    });

    expect(svc.saveWorkflow).toHaveBeenCalledWith(wf1);
    expect(returned).toEqual(saved);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it('enters error state when the save is rejected', async () => {
    const svc = { saveWorkflow: vi.fn().mockRejectedValue(new Error('conflict')) };
    const { result } = renderHook(() => useSaveWorkflow(), {
      wrapper: makeWrapper({ 'ting.workflows': svc }),
    });

    await act(async () => {
      await result.current.mutateAsync(wf1).catch(() => undefined);
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error?.message).toBe('conflict');
  });
});

describe('useWorkflowVersions', () => {
  it('returns the version list for a workflow id', async () => {
    const versions = [
      { version: '1.0.0', documentRevision: 'rev-1', createdAt: '2026-01-01', isHead: true },
    ];
    const svc = { listWorkflowVersions: vi.fn().mockResolvedValue(versions) };
    const { result } = renderHook(() => useWorkflowVersions(wf1.id), {
      wrapper: makeWrapper({ 'ting.workflows': svc }),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(versions);
    expect(svc.listWorkflowVersions).toHaveBeenCalledWith(wf1.id);
  });

  it('does not fetch when the id is empty', () => {
    const svc = { listWorkflowVersions: vi.fn() };
    const { result } = renderHook(() => useWorkflowVersions(''), {
      wrapper: makeWrapper({ 'ting.workflows': svc }),
    });
    expect(result.current.fetchStatus).toBe('idle');
    expect(svc.listWorkflowVersions).not.toHaveBeenCalled();
  });
});

describe('useLoadWorkflowVersion', () => {
  it('caches the loaded version under its own query key', async () => {
    const svc = { getWorkflowVersion: vi.fn().mockResolvedValue(wf1) };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(
        QueryClientProvider,
        { client },
        createElement(ServicesProvider, { services: { 'ting.workflows': svc } }, children),
      );
    const { result } = renderHook(() => useLoadWorkflowVersion(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync({ id: wf1.id, version: '1.0.0' });
    });

    expect(svc.getWorkflowVersion).toHaveBeenCalledWith(wf1.id, '1.0.0');
    expect(client.getQueryData(['ting', 'workflows', wf1.id, 'versions', '1.0.0'])).toEqual(wf1);
  });

  it('raises when the requested version was not found', async () => {
    const svc = { getWorkflowVersion: vi.fn().mockResolvedValue(null) };
    const { result } = renderHook(() => useLoadWorkflowVersion(), {
      wrapper: makeWrapper({ 'ting.workflows': svc }),
    });

    await act(async () => {
      await result.current.mutateAsync({ id: wf1.id, version: '9.9.9' }).catch(() => undefined);
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error?.message).toBe('Workflow version 9.9.9 was not found.');
  });
});

describe('usePreviewWorkflowImport', () => {
  it('previews an import source', async () => {
    const preview = {
      workflow: { id: wf1.id, name: wf1.name, description: undefined, version: undefined },
      personas: [],
      requirements: [],
      errors: [],
      canApply: true,
      previewDigest: 'digest-1',
    };
    const svc = { previewWorkflowImport: vi.fn().mockResolvedValue(preview) };
    const { result } = renderHook(() => usePreviewWorkflowImport(), {
      wrapper: makeWrapper({ 'ting.workflows': svc }),
    });

    await act(async () => {
      await result.current.mutateAsync({ content: 'name: Workflow 1', filename: 'wf.yaml' });
    });

    expect(svc.previewWorkflowImport).toHaveBeenCalledWith({
      content: 'name: Workflow 1',
      filename: 'wf.yaml',
    });
    await waitFor(() => expect(result.current.data).toEqual(preview));
  });
});

describe('useApplyWorkflowImport', () => {
  it('applies the import and seeds the workflow cache', async () => {
    const svc = { applyWorkflowImport: vi.fn().mockResolvedValue(wf1) };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(
        QueryClientProvider,
        { client },
        createElement(ServicesProvider, { services: { 'ting.workflows': svc } }, children),
      );
    const { result } = renderHook(() => useApplyWorkflowImport(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync({ content: 'name: Workflow 1', filename: 'wf.yaml' });
    });

    expect(svc.applyWorkflowImport).toHaveBeenCalledWith({
      content: 'name: Workflow 1',
      filename: 'wf.yaml',
    });
    expect(client.getQueryData(['ting', 'workflows', wf1.id])).toEqual(wf1);
  });
});

describe('useLaunchWorkflow', () => {
  it('launches the workflow with the given request', async () => {
    const launchResult = {
      workflowId: wf1.id,
      workflowName: wf1.name,
      slug: 'workflow-1',
      sessionId: 'session-1',
      sessionName: 'Workflow 1 run',
      status: 'started',
      clusterName: 'local',
      chatEndpoint: null,
      workflowVersion: '1.0.0',
      documentRevision: 'rev-1',
    };
    const svc = { launchWorkflow: vi.fn().mockResolvedValue(launchResult) };
    const { result } = renderHook(() => useLaunchWorkflow(), {
      wrapper: makeWrapper({ 'ting.workflows': svc }),
    });

    await act(async () => {
      await result.current.mutateAsync({ workflowId: wf1.id, request: { prompt: 'Ship it' } });
    });

    expect(svc.launchWorkflow).toHaveBeenCalledWith(wf1.id, { prompt: 'Ship it' });
    await waitFor(() => expect(result.current.data).toEqual(launchResult));
  });
});

describe('useExportWorkflow', () => {
  it('exports a pinned version in the requested format', async () => {
    const exported = { filename: 'workflow-1.yaml', content: 'name: Workflow 1' };
    const svc = { exportWorkflow: vi.fn().mockResolvedValue(exported) };
    const { result } = renderHook(() => useExportWorkflow(), {
      wrapper: makeWrapper({ 'ting.workflows': svc }),
    });

    await act(async () => {
      await result.current.mutateAsync({ id: wf1.id, format: 'yaml', version: '1.0.0' });
    });

    expect(svc.exportWorkflow).toHaveBeenCalledWith(wf1.id, 'yaml', '1.0.0');
    await waitFor(() => expect(result.current.data).toEqual(exported));
  });
});
