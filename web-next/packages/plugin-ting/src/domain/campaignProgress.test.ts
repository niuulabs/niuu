import { describe, expect, it } from 'vitest';
import {
  actionableSpecGates,
  activeStageLabel,
  pendingSpecGates,
  pendingWorkflowGates,
  progressPercent,
  statusForCampaign,
  type CampaignProgressInput,
} from './campaignProgress';

function campaign(overrides: Partial<CampaignProgressInput> = {}): CampaignProgressInput {
  return {
    status: 'running',
    activeStageId: null,
    stageState: [],
    metadata: {},
    ...overrides,
  };
}

describe('pendingWorkflowGates', () => {
  it('returns an empty list when metadata holds no gates', () => {
    expect(pendingWorkflowGates(campaign())).toEqual([]);
    expect(pendingWorkflowGates(null)).toEqual([]);
    expect(
      pendingWorkflowGates(campaign({ metadata: { pending_workflow_gates: 'nope' } })),
    ).toEqual([]);
  });

  it('accepts every key spelling the runtime writes', () => {
    const gates = pendingWorkflowGates(
      campaign({
        metadata: {
          pending_workflow_gates: [
            { gate_id: 'g1', node_id: 'plan-gate', label: 'Plan review', instructions: 'read it' },
            { gateId: 'g2', nodeId: 'qa-gate', summary: 'QA review' },
            { id: 'g3', node_id: 'ship-gate' },
            'not-an-object',
          ],
        },
      }),
    );
    expect(gates).toEqual([
      { id: 'g1', nodeId: 'plan-gate', summary: 'Plan review', instructions: 'read it' },
      { id: 'g2', nodeId: 'qa-gate', summary: 'QA review', instructions: '' },
      { id: 'g3', nodeId: 'ship-gate', summary: 'Review required', instructions: '' },
    ]);
  });
});

describe('pendingSpecGates', () => {
  const withGates = campaign({
    metadata: {
      pending_workflow_gates: [
        { id: 'g1', node_id: 'spec-prd-gate', summary: 'PRD review' },
        { node_id: 'spec-srd-gate', summary: 'SRD review' },
        { id: 'g3', node_id: 'build-gate', summary: 'Build review' },
      ],
    },
  });

  it('keeps only specification-stack gates', () => {
    expect(pendingSpecGates(withGates).map((gate) => gate.nodeId)).toEqual([
      'spec-prd-gate',
      'spec-srd-gate',
    ]);
  });

  it('drops gates without an id for the review call', () => {
    expect(actionableSpecGates(withGates).map((gate) => gate.id)).toEqual(['g1']);
  });
});

describe('statusForCampaign', () => {
  it('reports review while a spec gate waits', () => {
    expect(
      statusForCampaign(
        campaign({
          metadata: { pending_workflow_gates: [{ id: 'g1', node_id: 'spec-prd-gate' }] },
        }),
      ),
    ).toBe('review');
  });

  it('maps blocked, completed, pending and running', () => {
    expect(statusForCampaign(campaign({ status: 'blocked' }))).toBe('review');
    expect(statusForCampaign(campaign({ status: 'completed' }))).toBe('published');
    expect(statusForCampaign(campaign({ status: 'pending' }))).toBe('draft');
    expect(statusForCampaign(campaign({ status: 'running' }))).toBe('running');
  });
});

describe('progressPercent', () => {
  it('falls back to a sliver when no stages are known', () => {
    expect(progressPercent(campaign({ status: 'pending' }))).toBe(8);
    expect(progressPercent(campaign({ status: 'running' }))).toBe(18);
  });

  it('counts complete stages and credits an active one', () => {
    const percent = progressPercent(
      campaign({
        stageState: [
          { stageId: 'a', label: 'A', status: 'complete' },
          { stageId: 'b', label: 'B', status: 'active' },
          { stageId: 'c', label: 'C', status: 'pending' },
          { stageId: 'd', label: 'D', status: 'pending' },
        ],
      }),
    );
    expect(percent).toBe(38);
  });
});

describe('activeStageLabel', () => {
  it('prefers a blocked stage', () => {
    expect(
      activeStageLabel(
        campaign({
          stageState: [
            { stageId: 'a', label: 'A', status: 'complete' },
            { stageId: 'b', label: 'Review PRD', status: 'blocked' },
          ],
        }),
      ),
    ).toBe('Review PRD');
  });

  it('falls back to the active stage id when no label is set', () => {
    expect(
      activeStageLabel(
        campaign({
          activeStageId: 'spec-prd-draft',
          stageState: [{ stageId: 'spec-prd-draft', label: '', status: 'complete' }],
        }),
      ),
    ).toBe('prd draft');
  });

  it('describes an empty campaign', () => {
    expect(activeStageLabel(campaign({ status: 'pending' }))).toBe('Queued');
    expect(activeStageLabel(campaign({ status: 'running' }))).toBe('Starting');
  });
});
