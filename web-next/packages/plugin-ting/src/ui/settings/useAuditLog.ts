import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IAuditLogService, AuditFilter } from '../../ports';

export function useAuditLog(filter?: AuditFilter) {
  const audit = useService<IAuditLogService>('ting.audit');
  return useQuery({
    queryKey: ['ting', 'audit', filter],
    queryFn: () => audit.listAuditEntries(filter),
  });
}
