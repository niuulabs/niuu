import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { Ravn } from '../../domain/ravn';
import type {
  CreateResidentSessionRequest,
  DeployResidentRequest,
  IResidentControl,
  ResidentLifecycleAction,
} from '../../ports';

function useControl(): IResidentControl {
  return useService<IResidentControl>('ravn.residents');
}

export function useResidentProfiles(enabled = true) {
  const control = useControl();
  return useQuery({
    queryKey: ['ravn', 'resident-profiles'],
    queryFn: () => control.listProfiles(),
    enabled,
  });
}

export function useDeployResident() {
  const control = useControl();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: DeployResidentRequest) => control.deploy(request),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['ravn', 'ravens'] }),
        queryClient.invalidateQueries({ queryKey: ['ravn', 'sessions'] }),
      ]);
    },
  });
}

export function useDeployResidentFlock() {
  const control = useControl();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (requests: DeployResidentRequest[]) => {
      const results = await Promise.allSettled(requests.map((request) => control.deploy(request)));
      const deployed = results.flatMap((result) =>
        result.status === 'fulfilled' ? [result.value] : [],
      );
      const failure = results.find(
        (result): result is PromiseRejectedResult => result.status === 'rejected',
      );
      if (!failure) return deployed;
      await Promise.allSettled(deployed.map((ravn) => control.delete(ravn)));
      throw failure.reason;
    },
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['ravn', 'ravens'] }),
        queryClient.invalidateQueries({ queryKey: ['ravn', 'sessions'] }),
      ]);
    },
  });
}

export function useResidentLifecycle() {
  const control = useControl();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ ravn, action }: { ravn: Ravn; action: ResidentLifecycleAction }) =>
      control.applyLifecycle(ravn, action),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['ravn', 'ravens'] });
    },
  });
}

export function useDeleteResident() {
  const control = useControl();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (ravn: Ravn) => control.delete(ravn),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['ravn', 'ravens'] }),
        queryClient.invalidateQueries({ queryKey: ['ravn', 'sessions'] }),
      ]);
    },
  });
}

export function useDeleteResidentFlock() {
  const control = useControl();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (ravens: Ravn[]) => {
      const results = await Promise.allSettled(ravens.map((ravn) => control.delete(ravn)));
      const failed = results.flatMap((result, index) =>
        result.status === 'rejected' ? [ravens[index]!] : [],
      );
      if (failed.length === 0) return;

      throw new Error(`Failed to delete ${failed.length} of ${ravens.length} flock members`);
    },
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['ravn', 'ravens'] }),
        queryClient.invalidateQueries({ queryKey: ['ravn', 'sessions'] }),
      ]);
    },
  });
}

export function useResidentSessions(ravn: Ravn, enabled: boolean) {
  const control = useControl();
  return useQuery({
    queryKey: ['ravn', 'resident-sessions', ravn.id, ravn.instanceId],
    queryFn: () => control.listSessions(ravn),
    enabled,
  });
}

export function useResidentLogs(ravn: Ravn, enabled: boolean) {
  const control = useControl();
  return useQuery({
    queryKey: ['ravn', 'resident-logs', ravn.id, ravn.instanceId],
    queryFn: () => control.getLogs(ravn),
    enabled,
  });
}

export function useCreateResidentSession(ravn: Ravn) {
  const control = useControl();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: CreateResidentSessionRequest) => control.createSession(ravn, request),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: ['ravn', 'resident-sessions', ravn.id, ravn.instanceId],
        }),
        queryClient.invalidateQueries({ queryKey: ['ravn', 'sessions'] }),
        queryClient.invalidateQueries({ queryKey: ['ravn', 'ravens'] }),
      ]);
    },
  });
}

export function useDeleteResidentSession(ravn: Ravn) {
  const control = useControl();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) => control.deleteSession(ravn, sessionId),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: ['ravn', 'resident-sessions', ravn.id, ravn.instanceId],
        }),
        queryClient.invalidateQueries({ queryKey: ['ravn', 'sessions'] }),
        queryClient.invalidateQueries({ queryKey: ['ravn', 'ravens'] }),
      ]);
    },
  });
}
