import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { ITyrPersonaViewService } from '../../ports';

export function usePersonasBrowser(filter?: 'all' | 'builtin' | 'custom') {
  const personas = useService<ITyrPersonaViewService>('ravn.personas');
  return useQuery({
    queryKey: ['ravn', 'personas', filter ?? 'all'],
    queryFn: () => personas.listPersonas(filter),
  });
}

export function usePersonaYaml(name: string | null) {
  const personas = useService<ITyrPersonaViewService>('ravn.personas');
  return useQuery({
    queryKey: ['ravn', 'personas', 'yaml', name],
    queryFn: () => personas.getPersonaYaml(name!),
    enabled: name !== null,
  });
}
