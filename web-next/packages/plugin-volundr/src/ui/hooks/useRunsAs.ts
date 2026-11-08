import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IVolundrService } from '../../ports/IVolundrService';
import {
  offeredGrants,
  runsAsWorkloadConfig,
  type SessionGrant,
} from '../../domain/sessionRuntimes';

/**
 * What a launch on `instanceId` runs as: the one runtime the host runs every session
 * as, and the extra access the person asked for. Grants picked on one host never carry
 * to another. No host (tag routing), or a host that declares none, offers nothing.
 */
export function useRunsAs(instanceId: string | undefined) {
  const volundr = useService<IVolundrService>('volundr');
  const query = useQuery({
    queryKey: ['volundr', 'session-runtimes', instanceId],
    queryFn: () => volundr.getSessionRuntimes(instanceId),
    enabled: Boolean(instanceId),
  });
  const [picked, setPicked] = useState<{ host: string | undefined; grants: SessionGrant[] } | null>(
    null,
  );
  const offer = instanceId ? query.data?.[0] : undefined;
  const grants = offeredGrants(offer, picked && picked.host === instanceId ? picked.grants : []);
  return {
    offer,
    grants,
    error: instanceId ? query.error : null,
    /** Only while a host's runtime is unknown; the launch waits for it. */
    loading: Boolean(instanceId) && query.isLoading,
    setGrants: (next: SessionGrant[]) => setPicked({ host: instanceId, grants: next }),
    workloadConfig: runsAsWorkloadConfig(grants),
  };
}
