import type { ApiClient } from '@niuulabs/query';
import type { IDeliveryExecutionService } from '../ports';
import type { WorkflowExecution } from '../domain/workflowExecution';

export function buildWorkflowExecutionHttpAdapter(client: ApiClient): IDeliveryExecutionService {
  const base = '/workflow-executions';
  const path = (id: string) => `${base}/${encodeURIComponent(id)}`;
  return {
    list: ({ state, cursor } = {}) => {
      const params = new URLSearchParams();
      if (state) params.set('state', state);
      if (cursor) params.set('cursor', cursor);
      return client.get(`${base}${params.size ? `?${params}` : ''}`);
    },
    get: (id) => client.get<WorkflowExecution>(path(id)),
    launch: (request, idempotencyKey) =>
      client.post<WorkflowExecution>(base, request, {
        headers: { 'Idempotency-Key': idempotencyKey },
      }),
    cancel: (id) => client.post<WorkflowExecution>(`${path(id)}/cancel`, {}),
    reconcile: (id) => client.post<WorkflowExecution>(`${path(id)}/reconcile`, {}),
    retry: (id, childKey, attemptId) =>
      client.post<WorkflowExecution>(`${path(id)}/children/${encodeURIComponent(childKey)}/retry`, {
        attempt_id: attemptId,
      }),
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
