import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { WorkflowRegistryMount } from './WorkflowBuilder/mimirRegistry';

interface MimirRegistryService {
  mounts: {
    listRegistryMounts?: () => Promise<WorkflowRegistryMount[]>;
  };
}

export function useWorkflowRegistryMounts() {
  const service = useService<MimirRegistryService>('mimir');
  return useQuery({
    queryKey: ['mimir', 'registry', 'mounts'],
    queryFn: async () => {
      const listRegistryMounts = service.mounts.listRegistryMounts;
      if (!listRegistryMounts) return [];
      return listRegistryMounts.call(service.mounts);
    },
  });
}
