/**
 * Campaign progress helpers — pending gates, progress, stage labels, status.
 *
 * Spec and research campaigns share one shape (`SpecCampaign = ResearchCampaign`),
 * and three surfaces read the same derivations out of it: the specs center, the
 * spec detail page, and the Simple-mode workflows page. They live here so the
 * derivation exists once.
 *
 * Owner: plugin-ting.
 */

import type { CampaignStageState } from './research';

/** The subset of a campaign these helpers read. */
export interface CampaignProgressInput {
  status: 'pending' | 'running' | 'blocked' | 'completed' | 'failed';
  activeStageId?: string | null;
  stageState: CampaignStageState[];
  metadata: Record<string, unknown>;
}

export interface PendingWorkflowGate {
  id: string;
  nodeId: string;
  summary: string;
  instructions: string;
}

/** Campaign lifecycle as the operator-facing surfaces label it. */
export type CampaignReviewStatus = 'review' | 'running' | 'published' | 'draft';

/**
 * Every gate the runtime recorded as waiting on this campaign.
 *
 * The runtime writes `metadata.pending_workflow_gates`; key spellings differ
 * between producers, so all of them are accepted.
 */
export function pendingWorkflowGates(
  campaign: CampaignProgressInput | null | undefined,
): PendingWorkflowGate[] {
  const raw = campaign?.metadata.pending_workflow_gates;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((gate): gate is Record<string, unknown> => typeof gate === 'object' && gate !== null)
    .map((gate) => ({
      id: String(gate.id ?? gate.gate_id ?? gate.gateId ?? ''),
      nodeId: String(gate.node_id ?? gate.nodeId ?? ''),
      summary: String(gate.summary ?? gate.label ?? 'Review required'),
      instructions: String(gate.instructions ?? ''),
    }));
}

/** Pending gates that belong to the specification stack (`spec-<doc>-gate`). */
export function pendingSpecGates(
  campaign: CampaignProgressInput | null | undefined,
): PendingWorkflowGate[] {
  return pendingWorkflowGates(campaign).filter(
    (gate) => gate.nodeId.startsWith('spec-') && gate.nodeId.endsWith('-gate'),
  );
}

/**
 * Pending spec gates that can actually be resolved.
 *
 * `POST /specs/campaigns/{slug}/review` selects the gate by id, so a gate the
 * runtime recorded without one cannot be decided from the UI.
 */
export function actionableSpecGates(
  campaign: CampaignProgressInput | null | undefined,
): PendingWorkflowGate[] {
  return pendingSpecGates(campaign).filter((gate) => gate.id);
}

/** Where the campaign sits for filter chips and status lines. */
export function statusForCampaign(campaign: CampaignProgressInput): CampaignReviewStatus {
  if (pendingSpecGates(campaign).length > 0 || campaign.status === 'blocked') return 'review';
  if (campaign.status === 'completed') return 'published';
  if (campaign.status === 'pending') return 'draft';
  return 'running';
}

/** Rough completion, clamped so a just-started campaign still shows a sliver. */
export function progressPercent(campaign: CampaignProgressInput): number {
  const stages = campaign.stageState;
  if (!stages.length) return campaign.status === 'pending' ? 8 : 18;
  const complete = stages.filter((stage) => stage.status === 'complete').length;
  const active = stages.some((stage) => stage.status === 'active') ? 0.5 : 0;
  const blocked = stages.some((stage) => stage.status === 'blocked') ? 0.2 : 0;
  return Math.max(
    8,
    Math.min(100, Math.round(((complete + active + blocked) / stages.length) * 100)),
  );
}

/** The stage a reader should look at: blocked, else active, else next up. */
export function activeStageLabel(campaign: CampaignProgressInput): string {
  const stages = campaign.stageState;
  const active =
    stages.find((stage) => stage.status === 'blocked' || stage.status === 'active') ??
    stages.find((stage) => stage.stageId === campaign.activeStageId) ??
    stages.find((stage) => stage.status === 'pending') ??
    stages.at(-1);
  if (!active) return campaign.status === 'pending' ? 'Queued' : 'Starting';
  return active.label || active.stageId.replace(/^spec-/, '').replace(/-/g, ' ');
}
