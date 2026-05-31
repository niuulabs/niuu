import { z } from 'zod';

export const researchCampaignStatusSchema = z.enum([
  'pending',
  'running',
  'blocked',
  'completed',
  'failed',
]);
export type ResearchCampaignStatus = z.infer<typeof researchCampaignStatusSchema>;

export const campaignStageStateSchema = z.object({
  stageId: z.string(),
  label: z.string(),
  status: z.string(),
  startedAt: z.string().datetime().nullable().optional(),
  completedAt: z.string().datetime().nullable().optional(),
  reason: z.string().nullable().optional(),
});
export type CampaignStageState = z.infer<typeof campaignStageStateSchema>;

export const campaignArtifactSchema = z.object({
  path: z.string(),
  title: z.string(),
  updatedAt: z.string().datetime(),
  kind: z.string().nullable().optional(),
  publishState: z.string(),
  sourceIds: z.array(z.string()).default([]),
  summary: z.string().nullable().optional(),
});
export type CampaignArtifact = z.infer<typeof campaignArtifactSchema>;

export const researchCampaignSchema = z.object({
  id: z.string().uuid(),
  slug: z.string(),
  name: z.string(),
  ownerId: z.string(),
  workflowId: z.string().uuid(),
  workflowVersion: z.string(),
  workflowName: z.string(),
  sessionId: z.string(),
  sessionName: z.string(),
  status: researchCampaignStatusSchema,
  activeStageId: z.string().nullable().optional(),
  stageState: z.array(campaignStageStateSchema).default([]),
  metadata: z.record(z.string(), z.unknown()).default({}),
  createdAt: z.string().datetime(),
  updatedAt: z.string().datetime(),
  lastActivityAt: z.string().datetime().nullable().optional(),
  completedAt: z.string().datetime().nullable().optional(),
});
export type ResearchCampaign = z.infer<typeof researchCampaignSchema>;

export const researchCampaignDetailSchema = researchCampaignSchema.extend({
  artifacts: z.array(campaignArtifactSchema).default([]),
  canonicalArtifacts: z.record(z.string(), z.string()).default({}),
});
export type ResearchCampaignDetail = z.infer<typeof researchCampaignDetailSchema>;

export const campaignArtifactDetailSchema = campaignArtifactSchema.extend({
  content: z.string(),
});
export type CampaignArtifactDetail = z.infer<typeof campaignArtifactDetailSchema>;
