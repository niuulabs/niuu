import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useOptionalService } from '@niuulabs/plugin-sdk';
import type { IVolundrService } from '@niuulabs/plugin-volundr';
import { ravnSessionRequest, type RavnSessionLaunch } from '../../application/sessionLaunch';

/**
 * Start a ravn as a Forge flock session. Null when the host wires no Forge
 * service — then this way of running a ravn is simply not offered.
 */
export function useRavnSessionLaunch() {
  const volundr = useOptionalService<IVolundrService>('volundr');
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: async (launch: RavnSessionLaunch) => {
      if (!volundr) throw new Error('No Forge service is wired into this host');
      return volundr.startSession(ravnSessionRequest(launch));
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['ravn', 'sessions'] });
    },
  });
  return volundr ? mutation : null;
}
