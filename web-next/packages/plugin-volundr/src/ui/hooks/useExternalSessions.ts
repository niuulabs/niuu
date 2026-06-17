import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IVolundrService } from '../../ports/IVolundrService';

export const EXTERNAL_SESSIONS_QUERY_KEY = ['volundr', 'external-sessions'] as const;

/**
 * The backend answers 503 when external-session discovery is not enabled
 * (e.g. non-mini mode). Callers hide the import affordance instead of
 * surfacing an error.
 */
export function isExternalSessionsUnavailableError(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  return (error as { status?: number }).status === 503;
}

interface UseExternalSessionsOptions {
  enabled?: boolean;
}

/** Queries discoverable external CLI sessions (Claude Code / Codex) on the host. */
export function useExternalSessions(options: UseExternalSessionsOptions = {}) {
  const volundr = useService<IVolundrService>('volundr');
  return useQuery({
    queryKey: EXTERNAL_SESSIONS_QUERY_KEY,
    queryFn: () => volundr.listExternalSessions(),
    enabled: options.enabled ?? true,
    retry: false,
    refetchOnWindowFocus: false,
  });
}
