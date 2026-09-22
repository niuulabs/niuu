import type { ApiClient } from '@niuulabs/query';
import type { IWorkflowExecutionService, IDeliveryExecutionService } from '../ports';
import type { WorkflowExecution, DeliveryExecution } from '../domain/workflowExecution';

/** Generic durable workflow execution adapter — `/workflow-executions`. */
export function buildWorkflowExecutionHttpAdapter(client: ApiClient): IWorkflowExecutionService {
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
    waits: (id) => client.get(`${path(id)}/waits`),
    trace: (id, options = {}) => {
      const params = new URLSearchParams();
      if (options.childId) params.set('childId', options.childId);
      if (options.after !== undefined) params.set('after', String(options.after));
      if (options.limit !== undefined) params.set('limit', String(options.limit));
      return client.get(`${path(id)}/trace${params.size ? `?${params}` : ''}`);
    },
  };
}

/** Code-delivery execution adapter — `/delivery-executions`. */
export function buildDeliveryExecutionHttpAdapter(client: ApiClient): IDeliveryExecutionService {
  const base = '/delivery-executions';
  const path = (id: string) => `${base}/${encodeURIComponent(id)}`;
  return {
    get: (id) => client.get<DeliveryExecution>(path(id)),
    launch: (request, idempotencyKey) =>
      client.post<DeliveryExecution>(base, request, {
        headers: { 'Idempotency-Key': idempotencyKey },
      }),
    evidence: (id) => client.get<Record<string, unknown>>(`${path(id)}/evidence`),
  };
}
