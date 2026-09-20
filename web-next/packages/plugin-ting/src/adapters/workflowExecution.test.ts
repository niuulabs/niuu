import { describe, expect, it, vi } from 'vitest';
import { buildWorkflowExecutionHttpAdapter } from './workflowExecution';

describe('developer execution HTTP adapter', () => {
  it('uses the durable execution API and encodes all opaque handles', async () => {
    const client = {
      get: vi.fn().mockResolvedValue({}),
      post: vi.fn().mockResolvedValue({}),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
    };
    const service = buildWorkflowExecutionHttpAdapter(client);
    await service.list();
    expect(client.get).toHaveBeenLastCalledWith('/workflow-executions');
    await service.list({ state: 'blocked', cursor: 'a/b' });
    expect(client.get).toHaveBeenLastCalledWith('/workflow-executions?state=blocked&cursor=a%2Fb');
    await service.get('id/a');
    expect(client.get).toHaveBeenLastCalledWith('/workflow-executions/id%2Fa');
    const request = {
      workflowId: 'workflow',
      prompt: 'Fix it',
      repo: 'repo',
      baseBranch: 'target',
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
    await service.evidence('id');
    expect(client.get).toHaveBeenLastCalledWith('/workflow-executions/id/evidence');
    await service.deliveryWaits('id/a');
    expect(client.get).toHaveBeenLastCalledWith('/workflow-executions/id%2Fa/delivery-waits');
    await service.trace('id/a');
    expect(client.get).toHaveBeenLastCalledWith('/workflow-executions/id%2Fa/trace');
    await service.trace('id/a', { childId: 'child/a', after: 0, limit: 100 });
    expect(client.get).toHaveBeenLastCalledWith(
      '/workflow-executions/id%2Fa/trace?childId=child%2Fa&after=0&limit=100',
    );
  });
});
