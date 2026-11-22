import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { McpServerConfig } from '../../models/volundr.model';
import type { IVolundrService } from '../../ports/IVolundrService';

/** The MCP servers a Forge host's operator lets a session turn on. */
export function useSessionTools(instanceId: string | undefined, enabled = true) {
  const volundr = useService<IVolundrService>('volundr');
  return useQuery<McpServerConfig[], Error>({
    queryKey: ['volundr', 'mcp-servers', instanceId ?? null],
    queryFn: () => volundr.getAvailableMcpServers(instanceId ? { instanceId } : undefined),
    enabled,
  });
}

/** The chosen names this host offers: what is shown as on is exactly what is asked for. */
export function offeredTools(chosen: readonly string[], servers: readonly McpServerConfig[]) {
  const offered = new Set(servers.map((server) => server.name));
  return chosen.filter((name) => offered.has(name));
}
