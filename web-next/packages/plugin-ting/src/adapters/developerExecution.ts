import type { ApiClient } from '@niuulabs/query';
import type { IDeveloperExecutionService } from '../ports';
import type { DeveloperExecution } from '../domain/developerExecution';

export function buildDeveloperExecutionHttpAdapter(client: ApiClient): IDeveloperExecutionService {
  const base = '/developer-executions';
  const path = (id: string) => `${base}/${encodeURIComponent(id)}`;
  return {
    list: ({ state, cursor } = {}) => {
      const params = new URLSearchParams();
      if (state) params.set('state', state);
      if (cursor) params.set('cursor', cursor);
      return client.get(`${base}${params.size ? `?${params}` : ''}`);
    },
    get: (id) => client.get<DeveloperExecution>(path(id)),
    launch: (request, idempotencyKey) =>
      client.post<DeveloperExecution>(base, request, {
        headers: { 'Idempotency-Key': idempotencyKey },
      }),
    cancel: (id) => client.post<DeveloperExecution>(`${path(id)}/cancel`, {}),
    reconcile: (id) => client.post<DeveloperExecution>(`${path(id)}/reconcile`, {}),
    retry: (id, childKey, attemptId) =>
      client.post<DeveloperExecution>(
        `${path(id)}/children/${encodeURIComponent(childKey)}/retry`,
        { attempt_id: attemptId },
      ),
    evidence: (id) => client.get<Record<string, unknown>>(`${path(id)}/evidence`),
    deliveryWaits: (id) => client.get(`${path(id)}/delivery-waits`),
    trace: (id, options = {}) => {
      const params = new URLSearchParams();
      if (options.childId) params.set('childId', options.childId);
      if (options.after !== undefined) params.set('after', String(options.after));
      if (options.limit !== undefined) params.set('limit', String(options.limit));
      return client.get(`${path(id)}/trace${params.size ? `?${params}` : ''}`);
    },
  };
}
