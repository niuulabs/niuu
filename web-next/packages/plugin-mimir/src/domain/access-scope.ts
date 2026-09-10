import type { Mount } from '@niuulabs/domain';

export function accessScopeLabel(scope: Mount['accessScope']): string {
  return { tenant: 'Tenant', global: 'Global', local: 'Local', unknown: 'Scope unknown' }[
    scope ?? 'unknown'
  ];
}
