/**
 * useWorkflows — React Query wrappers for IWorkflowService.
 *
 * Owner: plugin-ting.
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { randomId } from '@niuulabs/ui';
import type {
  IWorkflowService,
  WorkflowExport,
  WorkflowExportFormat,
  WorkflowImportPreview,
  WorkflowImportSource,
  WorkflowLaunchRequest,
  WorkflowLaunchResult,
} from '../ports';
import type { Workflow } from '../domain/workflow';

export function useWorkflows() {
  const svc = useService<IWorkflowService>('ting.workflows');
  return useQuery({
    queryKey: ['ting', 'workflows'],
    queryFn: () => svc.listWorkflows(),
  });
}

export function useWorkflow(id: string) {
  const svc = useService<IWorkflowService>('ting.workflows');
  return useQuery({
    queryKey: ['ting', 'workflows', id],
    queryFn: () => svc.getWorkflow(id),
    enabled: !!id,
  });
}

export function useWorkflowVersions(id: string) {
  const svc = useService<IWorkflowService>('ting.workflows');
  return useQuery({
    queryKey: ['ting', 'workflows', id, 'versions'],
    queryFn: () => svc.listWorkflowVersions(id),
    enabled: !!id,
  });
}

export function useLoadWorkflowVersion() {
  const svc = useService<IWorkflowService>('ting.workflows');
  const queryClient = useQueryClient();
  return useMutation<Workflow, Error, { id: string; version: string }>({
    mutationFn: async ({ id, version }) => {
      const workflow = await svc.getWorkflowVersion(id, version);
      if (!workflow) throw new Error(`Workflow version ${version} was not found.`);
      return workflow;
    },
    onSuccess: (workflow, variables) => {
      queryClient.setQueryData(
        ['ting', 'workflows', variables.id, 'versions', variables.version],
        workflow,
      );
    },
  });
}

export function useCreateWorkflow() {
  const svc = useService<IWorkflowService>('ting.workflows');
  const queryClient = useQueryClient();
  return useMutation<Workflow, Error, Partial<Workflow> | undefined>({
    mutationFn: (seed): Promise<Workflow> => {
      const newWf: Workflow = {
        id: randomId(),
        name: 'New Workflow',
        nodes: [],
        edges: [],
        ...seed,
      };
      return svc.saveWorkflow(newWf);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['ting', 'workflows'] });
    },
  });
}

export function useSaveWorkflow() {
  const svc = useService<IWorkflowService>('ting.workflows');
  const queryClient = useQueryClient();
  return useMutation<Workflow, Error, Workflow>({
    mutationFn: (workflow: Workflow) => svc.saveWorkflow(workflow),
    onSuccess: (saved) => {
      queryClient.setQueryData(['ting', 'workflows', saved.id], saved);
      void queryClient.invalidateQueries({ queryKey: ['ting', 'workflows'] });
      void queryClient.invalidateQueries({
        queryKey: ['ting', 'workflows', saved.id, 'versions'],
      });
    },
  });
}

export function useDeleteWorkflow() {
  const svc = useService<IWorkflowService>('ting.workflows');
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (id: string) => svc.deleteWorkflow(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['ting', 'workflows'] });
    },
  });
}

export function useExportWorkflow() {
  const svc = useService<IWorkflowService>('ting.workflows');
  return useMutation<
    WorkflowExport,
    Error,
    { id: string; format: WorkflowExportFormat; version?: string }
  >({
    mutationFn: ({ id, format, version }) => svc.exportWorkflow(id, format, version),
  });
}

export function usePreviewWorkflowImport() {
  const svc = useService<IWorkflowService>('ting.workflows');
  return useMutation<WorkflowImportPreview, Error, WorkflowImportSource>({
    mutationFn: (request) => svc.previewWorkflowImport(request),
  });
}

export function useApplyWorkflowImport() {
  const svc = useService<IWorkflowService>('ting.workflows');
  const queryClient = useQueryClient();
  return useMutation<Workflow, Error, WorkflowImportSource>({
    mutationFn: (request) => svc.applyWorkflowImport(request),
    onSuccess: (workflow) => {
      queryClient.setQueryData(['ting', 'workflows', workflow.id], workflow);
      void queryClient.invalidateQueries({ queryKey: ['ting', 'workflows'] });
    },
  });
}

export function useLaunchWorkflow() {
  const svc = useService<IWorkflowService>('ting.workflows');
  return useMutation<
    WorkflowLaunchResult,
    Error,
    { workflowId: string; request: WorkflowLaunchRequest }
  >({
    mutationFn: ({ workflowId, request }) => svc.launchWorkflow(workflowId, request),
  });
}
