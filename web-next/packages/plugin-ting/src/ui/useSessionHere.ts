import { useQuery } from '@tanstack/react-query';
import { useOptionalService } from '@niuulabs/plugin-sdk';
import type { IRunLogService } from '../ports';

/** Whether this UI covers one Forge node, while Ting may know sessions on every Forge. */
export function useNodeScoped(): boolean {
  return useOptionalService<IRunLogService>('ting.runLog')?.scope === 'node';
}

/** The query that asks this node whether it has a session; the Guild answers for every host. */
export function sessionHereQuery(runLog: IRunLogService, sessionId: string) {
  return {
    queryKey: ['ting', 'session-here', sessionId],
    queryFn: async () => (await runLog.session(sessionId, null)) !== null,
  };
}

/**
 * Whether a session Ting names can be opened here. The Guild opens any host's; a node opens
 * its own, so it is asked (`undefined` until it answers). A failed answer counts as here: the
 * session's own page then shows what is wrong.
 */
export function useSessionHere(sessionId: string | null | undefined): boolean | undefined {
  const runLog = useOptionalService<IRunLogService>('ting.runLog');
  const ask = runLog?.scope === 'node' && Boolean(sessionId);
  const here = useQuery({
    ...sessionHereQuery(runLog as IRunLogService, sessionId ?? ''),
    enabled: ask,
  });
  if (!ask || here.isError) return true;
  return here.data;
}
