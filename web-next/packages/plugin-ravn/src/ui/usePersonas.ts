import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IPersonaStore } from '../ports';

export function usePersonas() {
  const service = useService<IPersonaStore>('ravn.personas');
  return useQuery({
    queryKey: ['ravn', 'personas'],
    queryFn: () => service.listPersonas(),
  });
}
