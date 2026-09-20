import { describe, expect, it, vi } from 'vitest';
import {
  buildWorkflowExecutionHttpAdapter,
  buildDeliveryExecutionHttpAdapter,
} from './workflowExecution';

function mockClient() {
  return {
    get: vi.fn().mockResolvedValue({}),
    post: vi.fn().mockResolvedValue({}),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  };
}

describe('generic workflow execution HTTP adapter', () => {
  it('uses the durable execution API and encodes all opaque handles', async () => {
    const client = mockClient();
    const service = buildWorkflowExecutionHttpAdapter(client);
    await service.list();
    expect(client.get).toHaveBeenLastCalledWith('/workflow-executions');
    await service.list({ state: 'blocked', cursor: 'a/b' });
    expect(client.get).toHaveBeenLastCalledWith('/workflow-executions?state=blocked&cursor=a%2Fb');
    await service.get('id/a');
    expect(client.get).toHaveBeenLastCalledWith('/workflow-executions/id%2Fa');
    const request = {
      workflowId: 'workflow',
      parentNodeId: 'expand',
      prompt: 'Fix it',
      input: { key: 'value' },
    };
    await service.launch(request, 'launch-key');
    expect(client.post).toHaveBeenLastCalledWith('/workflow-executions', request, {
      headers: { 'Idempotency-Key': 'launch-key' },
    });
    await service.cancel('id');
    expect(client.post).toHaveBeenLastCalledWith('/workflow-executions/id/cancel', {});
    await service.reconcile('id');
    expect(client.post).toHaveBeenLastCalledWith('/workflow-executions/id/reconcile', {});
    await service.retry('id', 'a/b', 'current-attempt');
    expect(client.post).toHaveBeenLastCalledWith('/workflow-executions/id/children/a%2Fb/retry', {
      attempt_id: 'current-attempt',
    });
    await service.waits('id/a');
    expect(client.get).toHaveBeenLastCalledWith('/workflow-executions/id%2Fa/waits');
    await service.trace('id/a');
    expect(client.get).toHaveBeenLastCalledWith('/workflow-executions/id%2Fa/trace');
    await service.trace('id/a', { childId: 'child/a', after: 0, limit: 100 });
    expect(client.get).toHaveBeenLastCalledWith(
      '/workflow-executions/id%2Fa/trace?childId=child%2Fa&after=0&limit=100',
    );
  });
});

describe('delivery execution HTTP adapter', () => {
  it('uses the code-delivery API and encodes all opaque handles', async () => {
    const client = mockClient();
    const service = buildDeliveryExecutionHttpAdapter(client);
    await service.get('id/a');
    expect(client.get).toHaveBeenLastCalledWith('/delivery-executions/id%2Fa');
    const request = {
      workflowId: 'workflow',
      parentNodeId: 'expand',
      prompt: 'Fix it',
      repo: 'repo',
      baseBranch: 'target',
    };
    await service.launch(request, 'launch-key');
    expect(client.post).toHaveBeenLastCalledWith('/delivery-executions', request, {
      headers: { 'Idempotency-Key': 'launch-key' },
    });
    await service.evidence('id/a');
    expect(client.get).toHaveBeenLastCalledWith('/delivery-executions/id%2Fa/evidence');
  });
});
