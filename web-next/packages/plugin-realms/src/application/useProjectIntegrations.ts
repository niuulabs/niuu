import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type {
  ForgeProject,
  IVolundrService,
  TeamDeliverTo,
  TeamRoute,
  TeamRouteEvent,
  TeamServiceAccountConnect,
} from '@niuulabs/plugin-volundr';

/** localStorage key the provider callback page sets, then removes, when an install finishes. */
export const PROVIDER_CONNECTED_KEY = 'niuu:provider-connected';

/** The value the callback page sets for a team install of `slug` on `projectId`. */
export const teamInstallSignal = (projectId: string, slug: string) => `team:${projectId}:${slug}`;

export const teamIntegrationsKey = (projectId: string) =>
  ['volundr', 'projects', projectId, 'integrations'] as const;

/** The API's own words for a failure: its `detail` when it sent one, else the error text. */
export function errorDetail(error: unknown): string {
  if (error && typeof error === 'object') {
    const detail = (error as { detail?: unknown }).detail;
    if (typeof detail === 'string' && detail.trim()) return detail;
    const message = (error as { message?: unknown }).message;
    if (typeof message === 'string' && message.trim()) return message;
  }
  return String(error);
}

/** Whether the API refused because the viewer is not an owner of the project. */
export function isForbidden(error: unknown): boolean {
  return !!error && typeof error === 'object' && (error as { status?: unknown }).status === 403;
}

/** What the project's owners can install for the team. */
export function useTeamCatalog(project: ForgeProject) {
  const volundr = useService<IVolundrService>('volundr');
  return useQuery({
    queryKey: [...teamIntegrationsKey(project.id), 'catalog'] as const,
    queryFn: () => volundr.getTeamIntegrationCatalog(project),
  });
}

/** The integrations the project owns. */
export function useTeamIntegrations(project: ForgeProject) {
  const volundr = useService<IVolundrService>('volundr');
  return useQuery({
    queryKey: [...teamIntegrationsKey(project.id), 'connections'] as const,
    queryFn: () => volundr.getTeamIntegrations(project),
  });
}

/** Where each kind of event goes. */
export function useTeamRoutes(project: ForgeProject) {
  const volundr = useService<IVolundrService>('volundr');
  return useQuery({
    queryKey: [...teamIntegrationsKey(project.id), 'routes'] as const,
    queryFn: () => volundr.getTeamRoutes(project),
  });
}

/** Uses of the team's connections, newest page first; older pages load on request. */
export function useTeamUsage(project: ForgeProject) {
  const volundr = useService<IVolundrService>('volundr');
  return useInfiniteQuery({
    queryKey: [...teamIntegrationsKey(project.id), 'usage'] as const,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => volundr.getTeamIntegrationUsage(project, pageParam),
    getNextPageParam: (page) => page.next_before ?? undefined,
  });
}

function useInvalidateIntegrations(projectId: string) {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: teamIntegrationsKey(projectId) });
}

export function useConnectServiceAccount(project: ForgeProject) {
  const volundr = useService<IVolundrService>('volundr');
  const invalidate = useInvalidateIntegrations(project.id);
  return useMutation({
    mutationFn: (connect: TeamServiceAccountConnect) =>
      volundr.connectTeamServiceAccount(project, connect),
    onSuccess: invalidate,
  });
}

/** Asks Forge for the provider's approval url; refreshing waits for the callback's signal. */
export function useStartTeamOAuth(project: ForgeProject) {
  const volundr = useService<IVolundrService>('volundr');
  return useMutation({
    mutationFn: ({ slug, config }: { slug: string; config: Record<string, string> }) =>
      volundr.startTeamOAuth(project, slug, config),
  });
}

export function useRemoveTeamIntegration(project: ForgeProject) {
  const volundr = useService<IVolundrService>('volundr');
  const invalidate = useInvalidateIntegrations(project.id);
  return useMutation({
    mutationFn: (connectionId: string) => volundr.removeTeamIntegration(project, connectionId),
    onSuccess: invalidate,
  });
}

/** Route an event somewhere (PUT) or nowhere (DELETE). */
export function useSetTeamRoute(project: ForgeProject) {
  const volundr = useService<IVolundrService>('volundr');
  const invalidate = useInvalidateIntegrations(project.id);
  return useMutation({
    mutationFn: async ({
      event,
      route,
    }: {
      event: TeamRouteEvent;
      route: { connectionId: string; deliverTo: TeamDeliverTo; channel: string } | null;
    }): Promise<void> => {
      if (!route) return volundr.removeTeamRoute(project, event);
      await volundr.setTeamRoute(project, event, {
        connection_id: route.connectionId,
        deliver_to: route.deliverTo,
        channel: route.channel,
        enabled: true,
      });
    },
    onSuccess: invalidate,
  });
}

/** The route of `event` for one connection, if it has one that is on. */
export function routeFor(
  routes: readonly TeamRoute[],
  connectionId: string,
  event: TeamRouteEvent,
): TeamRoute | undefined {
  return routes.find(
    (route) => route.event === event && route.connection_id === connectionId && route.enabled,
  );
}
