import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IVolundrService } from '../../ports/IVolundrService';
import {
  COMPUTE_HOSTS_POLL_MS,
  type ComputeHostChanges,
  type PairingCodeRequest,
} from '../../domain/computeHosts';

export function computeHostsKey(instanceId: string) {
  return ['volundr', 'compute-hosts', instanceId] as const;
}

/**
 * The Forge's compute hosts, re-read while the view is open. The data is `null` for a Forge
 * that has none (it answers 404): that Forge has no Hosts view.
 */
export function useComputeHosts(instanceId: string, refetchMs: number = COMPUTE_HOSTS_POLL_MS) {
  const volundr = useService<IVolundrService>('volundr');
  return useQuery({
    queryKey: computeHostsKey(instanceId),
    queryFn: () => volundr.getComputeHosts(instanceId),
    refetchInterval: refetchMs,
  });
}

/** How many hosts a Forge has and how many of their slots are free. */
export interface ComputeHostsCount {
  hosts: number;
  slotsFree: number;
}

/**
 * The Forge's host count and free slots once it has answered that it has compute hosts;
 * null while unanswered, for a Forge without hosts, or when it cannot be read.
 */
export function useComputeHostsCount(instanceId: string): ComputeHostsCount | null {
  const volundr = useService<IVolundrService>('volundr');
  const query = useQuery({
    queryKey: [...computeHostsKey(instanceId), 'available'],
    queryFn: async (): Promise<ComputeHostsCount | null> => {
      const overview = await volundr.getComputeHosts(instanceId);
      if (overview === null) return null;
      return { hosts: overview.summary.hosts, slotsFree: overview.summary.slotsFree };
    },
  });
  return query.data ?? null;
}

/** Every host action, each refreshing the list when it lands. */
export function useComputeHostActions(instanceId: string) {
  const volundr = useService<IVolundrService>('volundr');
  const queryClient = useQueryClient();
  const refresh = () => queryClient.invalidateQueries({ queryKey: computeHostsKey(instanceId) });

  return {
    createCode: useMutation({
      mutationFn: (request: PairingCodeRequest) => volundr.createPairingCode(instanceId, request),
      onSuccess: refresh,
    }),
    revokeCode: useMutation({
      mutationFn: (codeId: string) => volundr.revokePairingCode(instanceId, codeId),
      onSuccess: refresh,
    }),
    update: useMutation({
      mutationFn: (args: { hostId: string; changes: ComputeHostChanges }) =>
        volundr.updateComputeHost(instanceId, args.hostId, args.changes),
      onSuccess: refresh,
    }),
    drain: useMutation({
      mutationFn: (args: { hostId: string; remove: boolean }) =>
        volundr.drainComputeHost(instanceId, args.hostId, args.remove),
      onSuccess: refresh,
    }),
    resume: useMutation({
      mutationFn: (hostId: string) => volundr.resumeComputeHost(instanceId, hostId),
      onSuccess: refresh,
    }),
    reprepare: useMutation({
      mutationFn: (hostId: string) => volundr.reprepareComputeHost(instanceId, hostId),
      onSuccess: refresh,
    }),
    check: useMutation({
      mutationFn: (hostId: string) => volundr.checkComputeHost(instanceId, hostId),
      onSuccess: refresh,
    }),
    remove: useMutation({
      mutationFn: (hostId: string) => volundr.removeComputeHost(instanceId, hostId),
      onSuccess: refresh,
    }),
  };
}
