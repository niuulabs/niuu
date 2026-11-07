import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { ErrorState, LoadingState } from '@niuulabs/ui';
import type { IVolundrService } from '../../ports/IVolundrService';
import { errorText } from '../errorText';
import { ComputeHostsPanel } from './ComputeHostsPanel';
import { useComputeHosts } from './useComputeHosts';

function Notice({ children }: { children: string }) {
  return (
    <p className="niuu:rounded-xl niuu:border niuu:border-dashed niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-6 niuu:text-sm niuu:text-text-secondary">
      {children}
    </p>
  );
}

function HostsOfForge({ instanceId, forgeName }: { instanceId: string; forgeName: string }) {
  const hosts = useComputeHosts(instanceId);
  return (
    <>
      <div className="niuu:text-xs niuu:text-text-muted">
        Platform › Forges › <b className="niuu:font-medium niuu:text-text-secondary">{forgeName}</b>{' '}
        › Compute
      </div>
      <h1 className="niuu:mb-0.5 niuu:mt-1 niuu:text-xl niuu:font-semibold niuu:text-text-primary">
        {forgeName} Hosts
      </h1>
      <p className="niuu:mb-4 niuu:text-sm niuu:text-text-secondary">
        Machines registered as compute for this Forge. Sessions go into free slots; hosts are
        cleaned, never deleted.
      </p>
      {hosts.data === null ? (
        <Notice>This Forge has no compute hosts configured.</Notice>
      ) : (
        <ComputeHostsPanel instanceId={instanceId} />
      )}
    </>
  );
}

/**
 * The Hosts page of one Forge: `/volundr/hosts?instance_id=…`. Without an instance it
 * shows the default Forge.
 */
export function ComputeHostsPage({ instanceId }: { instanceId?: string }) {
  const volundr = useService<IVolundrService>('volundr');
  const targets = useQuery({
    queryKey: ['volundr', 'targets'],
    queryFn: () => volundr.getTargets(),
  });

  if (targets.isLoading) return <LoadingState label="Loading Forges…" />;
  if (targets.error) {
    return <ErrorState title="Failed to load Forges" message={errorText(targets.error)} />;
  }
  const forge = instanceId
    ? targets.data?.find((target) => target.id === instanceId)
    : targets.data?.find((target) => target.isDefault);
  const id = instanceId ?? forge?.id;

  return (
    <div className="niuu:min-h-full niuu:bg-bg-primary niuu:p-6" data-testid="compute-hosts-page">
      {id ? (
        <HostsOfForge instanceId={id} forgeName={forge?.name ?? id} />
      ) : (
        <Notice>No Forge is selected. Open a Forge from Platform to see its hosts.</Notice>
      )}
    </div>
  );
}
