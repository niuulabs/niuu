import { useQuery } from '@tanstack/react-query';
import { useOptionalService, useService } from '@niuulabs/plugin-sdk';
import type { IPersonaStore } from '../ports';

export function usePersonas(enabled = true) {
  const service = useService<IPersonaStore>('ravn.personas');
  return useQuery({
    queryKey: ['ravn', 'personas'],
    queryFn: () => service.listPersonas(),
    enabled,
  });
}

export function useOptionalPersonas(enabled = true) {
  const service = useOptionalService<IPersonaStore>('ravn.personas');
  return useQuery({
    queryKey: ['ravn', 'personas'],
    queryFn: () => service?.listPersonas() ?? Promise.resolve([]),
    enabled: enabled && Boolean(service),
  });
}
