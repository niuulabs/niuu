import { useQuery } from '@tanstack/react-query';
import { Info } from 'lucide-react';
import { Popover, PopoverContent, PopoverTrigger } from '@niuulabs/ui';
import type { IVolundrService } from '../ports/IVolundrService';
import type { VolundrSession } from '../models/volundr.model';
import './SessionEnvironment.css';

export function SessionRuntimeVersion({
  session,
  service,
}: {
  session: VolundrSession;
  service: IVolundrService;
}) {
  const query = useQuery({
    queryKey: ['volundr', 'runtime-version', session.instanceId, session.id, session.status],
    queryFn: () => service.getRuntimeVersion(session.id, session.instanceId),
    retry: false,
    staleTime: 60_000,
  });
  const version = query.data;
  const label = version?.state === 'different' ? 'Runtime update available' : 'Runtime version';
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button type="button" className="forge-environment-trigger" title={label}>
          <Info size={15} />
          <span>{label}</span>
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="forge-environment-popover">
        <strong>Skuld runtime</strong>
        <p role="status">
          {query.isPending
            ? 'Checking runtime version…'
            : query.isError
              ? 'This Forge could not report its runtime version.'
              : version?.state === 'current'
                ? 'This session is using the available build.'
                : version?.state === 'different'
                  ? 'This session is still using a different build. It has not been restarted.'
                  : version?.state === 'not_running'
                    ? 'No live runtime endpoint is registered.'
                    : 'The running version could not be verified.'}
        </p>
        <dl>
          <dt>Running</dt>
          <dd>{version?.current?.revision?.slice(0, 8) ?? 'Unknown'}</dd>
          <dt>Available on this Forge</dt>
          <dd>{version?.available?.revision?.slice(0, 8) ?? 'Unknown'}</dd>
        </dl>
        <p>
          New starts use the available build. To refresh an existing session, wait for its work to
          finish, then stop and resume it using the session controls.
        </p>
        <button
          type="button"
          onClick={() => void query.refetch()}
          disabled={query.isFetching}
          className="forge-runtime-check"
        >
          Check again
        </button>
      </PopoverContent>
    </Popover>
  );
}
