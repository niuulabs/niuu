import { useOptionalService } from '@niuulabs/plugin-sdk';
import type { IVolundrService } from '../../ports/IVolundrService';
import { useComputeHostsCount } from './useComputeHosts';

/** Where a Forge's Hosts page lives; the search of the current page (config, identity) carries over. */
export function computeHostsHref(instanceId: string, currentSearch: string): string {
  const params = new URLSearchParams(currentSearch);
  params.delete('register');
  params.set('instance_id', instanceId);
  return `/volundr/hosts?${params.toString()}`;
}

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? '' : 's'}`;
}

function HostsLink({
  instanceId,
  className,
  variant,
}: {
  instanceId: string;
  className?: string;
  variant: ForgeHostsLinkVariant;
}) {
  const count = useComputeHostsCount(instanceId);
  if (!count) return null;
  const href = computeHostsHref(instanceId, window.location.search);
  if (variant === 'link') {
    return (
      <a href={href} className={className}>
        Compute hosts
      </a>
    );
  }
  return (
    <a
      href={href}
      className={className}
      aria-label={`Hosts: ${plural(count.hosts, 'host')}, ${plural(count.slotsFree, 'free slot')}`}
    >
      Hosts · {count.hosts} · {count.slotsFree} free
    </a>
  );
}

/** `link` is the plain "Compute hosts" link; `chip` adds the host count and free slots. */
export type ForgeHostsLinkVariant = 'link' | 'chip';

/**
 * A link to the Forge's Hosts page, shown only once the Forge has answered that it has
 * compute hosts. Renders nothing without a Forge service or for a Forge without hosts.
 */
export function ForgeHostsLink({
  instanceId,
  className,
  variant = 'link',
}: {
  instanceId: string;
  className?: string;
  variant?: ForgeHostsLinkVariant;
}) {
  const volundr = useOptionalService<IVolundrService>('volundr');
  if (!volundr) return null;
  return <HostsLink instanceId={instanceId} className={className} variant={variant} />;
}
