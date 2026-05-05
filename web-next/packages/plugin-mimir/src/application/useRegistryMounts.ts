import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService } from '../ports';
import type { RegistryMount } from '../domain/registry';

export function useRegistryMounts() {
  const service = useService<IMimirService>('mimir');
  return useQuery({
    queryKey: ['mimir', 'registry', 'mounts'],
    queryFn: async () => {
      const listRegistryMounts = service.mounts.listRegistryMounts;
      if (!listRegistryMounts) return [];
      return listRegistryMounts.call(service.mounts);
    },
  });
}

export function useCreateRegistryMount() {
  const service = useService<IMimirService>('mimir');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (mount: Omit<RegistryMount, 'id'>) => {
      const createRegistryMount = service.mounts.createRegistryMount;
      if (!createRegistryMount) {
        throw new Error('Registry management is not available for this Mimir service.');
      }
      return createRegistryMount.call(service.mounts, mount);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['mimir', 'registry', 'mounts'] });
      void queryClient.invalidateQueries({ queryKey: ['mimir', 'mounts'] });
    },
  });
}

export function useUpdateRegistryMount() {
  const service = useService<IMimirService>('mimir');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, mount }: { id: string; mount: Omit<RegistryMount, 'id'> }) => {
      const updateRegistryMount = service.mounts.updateRegistryMount;
      if (!updateRegistryMount) {
        throw new Error('Registry management is not available for this Mimir service.');
      }
      return updateRegistryMount.call(service.mounts, id, mount);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['mimir', 'registry', 'mounts'] });
      void queryClient.invalidateQueries({ queryKey: ['mimir', 'mounts'] });
    },
  });
}

export function useDeleteRegistryMount() {
  const service = useService<IMimirService>('mimir');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const deleteRegistryMount = service.mounts.deleteRegistryMount;
      if (!deleteRegistryMount) {
        throw new Error('Registry management is not available for this Mimir service.');
      }
      return deleteRegistryMount.call(service.mounts, id);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['mimir', 'registry', 'mounts'] });
      void queryClient.invalidateQueries({ queryKey: ['mimir', 'mounts'] });
    },
  });
}
