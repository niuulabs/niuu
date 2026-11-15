import { describe, expect, it, vi } from 'vitest';
import { buildRunGateService } from './runGates';
import type { IWorkService } from '../ports';

function revision(status: string, number = 1) {
  return {
    number,
    gateId: 'approve',
    activationId: `activation-${number}`,
    requestedAt: '2026-11-09T12:00:00Z',
    status,
    summary: `revision ${number}`,
    decidedBy: '',
    notes: '',
    decidedAt: '',
    fields: {},
  };
}

function graphWith(states: Record<string, string>) {
  return {
    workflowName: 'Code, Review and Merge',
    nodes: Object.keys(states).map((id) => ({ id, label: `Step ${id}` })),
    edges: [],
    states: new Map(
      Object.entries(states).map(([nodeId, state]) => [nodeId, { nodeId, state, members: [] }]),
    ),
  };
}

function work(revisions: ReturnType<typeof revision>[], states: Record<string, string> = {}) {
  return {
    runGraph: vi.fn(async () => graphWith(states)),
    runReviews: vi.fn(async () => ({
      run: {},
      unreadableRuns: [],
      reviews: [{ columnId: 'approve', title: 'Approve merge', revisions }],
    })),
    decideRunGate: vi.fn(async () => {}),
  } as unknown as IWorkService & {
    decideRunGate: ReturnType<typeof vi.fn>;
  };
}

describe('run gates for a host that shows the run', () => {
  it("reads the run's newest pending revision as its waiting gate", async () => {
    const gates = buildRunGateService(work([revision('approved', 1), revision('pending', 2)]));

    expect(await gates.waitingGate('run-1')).toEqual({
      gateId: 'approve',
      activationId: 'activation-2',
      title: 'Approve merge',
      summary: 'revision 2',
      requestedAt: '2026-11-09T12:00:00Z',
    });
  });

  it('has no waiting gate once its newest revision is decided', async () => {
    const gates = buildRunGateService(work([revision('pending', 1), revision('approved', 2)]));

    expect(await gates.waitingGate('run-1')).toBeNull();
  });

  it('decides the gate as the review page does', async () => {
    const service = work([revision('pending')]);
    const gates = buildRunGateService(service);

    await gates.decide(
      'run-1',
      { gateId: 'approve', activationId: 'activation-1' },
      'request_changes',
      'Rename it',
    );

    expect(service.decideRunGate).toHaveBeenCalledWith('run-1', {
      gateId: 'approve',
      activationId: 'activation-1',
      decision: 'request_changes',
      notes: 'Rename it',
    });
  });

  it("names the step a run's stopped sessions hold", async () => {
    const gates = buildRunGateService(
      work([], { 'code-implement': 'done', 'code-merge': 'paused', 'code-merged': 'pending' }),
    );

    expect(await gates.pausedStep('run-1')).toEqual({
      nodeId: 'code-merge',
      title: 'Step code-merge',
    });
  });

  it('has no paused step while the run is at work', async () => {
    const gates = buildRunGateService(work([], { 'code-merge': 'active' }));

    expect(await gates.pausedStep('run-1')).toBeNull();
  });
});
