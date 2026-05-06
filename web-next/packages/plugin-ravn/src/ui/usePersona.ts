import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IPersonaStore, PersonaCreateRequest } from '../ports';

export function usePersona(name: string) {
  const service = useService<IPersonaStore>('ravn.personas');
  return useQuery({
    queryKey: ['ravn', 'personas', name],
    queryFn: () => service.getPersona(name),
    enabled: Boolean(name),
  });
}

export function usePersonaYaml(name: string) {
  const service = useService<IPersonaStore>('ravn.personas');
  return useQuery({
    queryKey: ['ravn', 'personas', name, 'yaml'],
    queryFn: () => service.getPersonaYaml(name),
    enabled: Boolean(name),
  });
}

export function useUpdatePersona(name: string) {
  const service = useService<IPersonaStore>('ravn.personas');
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (req: PersonaCreateRequest) => service.updatePersona(name, req),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['ravn', 'personas'] });
    },
  });
}

export function useCreatePersona() {
  const service = useService<IPersonaStore>('ravn.personas');
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (req: PersonaCreateRequest) => service.createPersona(req),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['ravn', 'personas'] });
    },
  });
}

export function useForkPersona(name: string) {
  const service = useService<IPersonaStore>('ravn.personas');
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (newName: string) => service.forkPersona(name, { newName }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['ravn', 'personas'] });
    },
  });
}
