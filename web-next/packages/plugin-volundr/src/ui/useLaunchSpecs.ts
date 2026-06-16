import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IVolundrService } from '../ports/IVolundrService';
import type { LaunchScope } from '../models/volundr.model';

/** Queries launch specs from the Völundr catalog/runtime service. */
export function useLaunchSpecs(scope?: LaunchScope) {
  const volundr = useService<IVolundrService>('volundr');
  return useQuery({
    queryKey: ['volundr', 'launch-specs', scope ?? 'all'],
    queryFn: () => volundr.getLaunchSpecs(scope),
  });
}
