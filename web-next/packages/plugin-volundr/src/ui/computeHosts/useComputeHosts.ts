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

/** True only once the Forge has answered that it has compute hosts. */
export function useHasComputeHosts(instanceId: string): boolean {
  const volundr = useService<IVolundrService>('volundr');
  const query = useQuery({
    queryKey: [...computeHostsKey(instanceId), 'available'],
    queryFn: async () => (await volundr.getComputeHosts(instanceId)) !== null,
  });
  return query.data === true;
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
