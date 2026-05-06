import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IAuditLogService, AuditFilter } from '../../ports';

export function useAuditLog(filter?: AuditFilter) {
  const audit = useService<IAuditLogService>('tyr.audit');
  return useQuery({
    queryKey: ['tyr', 'audit', filter],
    queryFn: () => audit.listAuditEntries(filter),
  });
}
