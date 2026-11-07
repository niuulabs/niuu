import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IVolundrService } from '../../ports/IVolundrService';
import {
  runsAsWorkloadConfig,
  selectedGrants,
  selectedOffer,
  type RunsAsSelection,
  type SessionGrant,
  type SessionRuntimeOffer,
} from '../../domain/sessionRuntimes';

/**
 * The "Runs as" choice for a launch on `instanceId`: what the host offers and what the
 * person picked. A choice made on one host never carries to another; there the host's
 * default applies until the person picks again. No host (tag routing) offers no choice.
 */
export function useRunsAs(instanceId: string | undefined) {
  const volundr = useService<IVolundrService>('volundr');
  const query = useQuery({
    queryKey: ['volundr', 'session-runtimes', instanceId],
    queryFn: () => volundr.getSessionRuntimes(instanceId),
    enabled: Boolean(instanceId),
  });
  const [selection, setSelection] = useState<RunsAsSelection | null>(null);
  const offers = instanceId ? (query.data ?? []) : [];
  const offer = selectedOffer(offers, selection);
  const grants = selectedGrants(offer, selection);
  return {
    offers,
    offer,
    grants,
    error: instanceId ? query.error : null,
    /** Only while a host's offers are unknown; the launch waits for them. */
    loading: Boolean(instanceId) && query.isLoading,
    select: (next: SessionRuntimeOffer) =>
      setSelection({ executionProfile: next.executionProfile, grants: [] }),
    setGrants: (next: SessionGrant[]) =>
      offer && setSelection({ executionProfile: offer.executionProfile, grants: next }),
    workloadConfig: runsAsWorkloadConfig(offer, grants),
  };
}
