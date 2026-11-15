import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IPersonaStore } from '../ports';

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

/** The persona's whole document, as written: what the editor starts from. */
export function usePersonaDocument(name: string) {
  const service = useService<IPersonaStore>('ravn.personas');
  return useQuery({
    queryKey: ['ravn', 'personas', name, 'document'],
    queryFn: () => service.getPersonaDocument(name),
    enabled: Boolean(name),
    // The editor holds its own copy; a refetch must not replace what is being edited.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
}

/** The server's verdict on YAML, as saving it would judge it. */
export function usePersonaYamlCheck(yaml: string, enabled: boolean) {
  const service = useService<IPersonaStore>('ravn.personas');
  return useQuery({
    queryKey: ['ravn', 'personas', 'yaml-check', yaml],
    queryFn: () => service.checkPersonaYaml(yaml),
    enabled: enabled && Boolean(yaml.trim()),
    staleTime: Infinity,
  });
}

/** Save a persona's YAML: create it, or replace the named persona. */
export function useSavePersonaYaml(name: string | null) {
  const service = useService<IPersonaStore>('ravn.personas');
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (yaml: string) =>
      name ? service.replacePersonaYaml(name, yaml) : service.createPersonaFromYaml(yaml),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['ravn', 'personas'] });
    },
  });
}

export function useDeletePersona(name: string) {
  const service = useService<IPersonaStore>('ravn.personas');
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () => service.deletePersona(name),
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
