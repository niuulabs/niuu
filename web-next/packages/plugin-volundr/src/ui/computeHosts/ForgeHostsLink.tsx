import { useOptionalService } from '@niuulabs/plugin-sdk';
import type { IVolundrService } from '../../ports/IVolundrService';
import { useHasComputeHosts } from './useComputeHosts';

/** Where a Forge's Hosts page lives; the search of the current page (config, identity) carries over. */
export function computeHostsHref(instanceId: string, currentSearch: string): string {
  const params = new URLSearchParams(currentSearch);
  params.delete('register');
  params.set('instance_id', instanceId);
  return `/volundr/hosts?${params.toString()}`;
}

function HostsLink({ instanceId, className }: { instanceId: string; className?: string }) {
  const available = useHasComputeHosts(instanceId);
  if (!available) return null;
  return (
    <a href={computeHostsHref(instanceId, window.location.search)} className={className}>
      Compute hosts
    </a>
  );
}

/**
 * A link to the Forge's Hosts page, shown only once the Forge has answered that it has
 * compute hosts. Renders nothing without a Forge service or for a Forge without hosts.
 */
export function ForgeHostsLink({
  instanceId,
  className,
}: {
  instanceId: string;
  className?: string;
}) {
  const volundr = useOptionalService<IVolundrService>('volundr');
  if (!volundr) return null;
  return <HostsLink instanceId={instanceId} className={className} />;
}
