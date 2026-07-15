import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { AgentDirectoryFilters } from '../domain';
import type { IAgentDirectory } from '../ports';

export function useAgents(filters: AgentDirectoryFilters = {}) {
  const directory = useService<IAgentDirectory>('observatory.agents');
  return useQuery({
    queryKey: ['observatory', 'agents', filters],
    queryFn: () => directory.listAgents(filters),
  });
}

export function useAgent(agentId: string) {
  const directory = useService<IAgentDirectory>('observatory.agents');
  return useQuery({
    queryKey: ['observatory', 'agents', agentId],
    queryFn: () => directory.getAgent(agentId),
    enabled: Boolean(agentId),
  });
}
