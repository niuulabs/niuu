/**
 * useWorkflows — React Query wrappers for IWorkflowService.
 *
 * Owner: plugin-tyr.
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IWorkflowService } from '../ports';
import type { Workflow } from '../domain/workflow';

export function useWorkflows() {
  const svc = useService<IWorkflowService>('tyr.workflows');
  return useQuery({
    queryKey: ['tyr', 'workflows'],
    queryFn: () => svc.listWorkflows(),
  });
}

export function useWorkflow(id: string) {
  const svc = useService<IWorkflowService>('tyr.workflows');
  return useQuery({
    queryKey: ['tyr', 'workflows', id],
    queryFn: () => svc.getWorkflow(id),
    enabled: !!id,
  });
}

export function useCreateWorkflow() {
  const svc = useService<IWorkflowService>('tyr.workflows');
  const queryClient = useQueryClient();
  return useMutation<Workflow, Error, void>({
    mutationFn: (): Promise<Workflow> => {
      const newWf: Workflow = {
        id: crypto.randomUUID(),
        name: 'New Workflow',
        nodes: [],
        edges: [],
      };
      return svc.saveWorkflow(newWf);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['tyr', 'workflows'] });
    },
  });
}

export function useSaveWorkflow() {
  const svc = useService<IWorkflowService>('tyr.workflows');
  const queryClient = useQueryClient();
  return useMutation<Workflow, Error, Workflow>({
    mutationFn: (workflow: Workflow) => svc.saveWorkflow(workflow),
    onSuccess: (saved) => {
      queryClient.setQueryData(['tyr', 'workflows', saved.id], saved);
      void queryClient.invalidateQueries({ queryKey: ['tyr', 'workflows'] });
    },
  });
}

export function useDeleteWorkflow() {
  const svc = useService<IWorkflowService>('tyr.workflows');
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (id: string) => svc.deleteWorkflow(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['tyr', 'workflows'] });
    },
  });
}
