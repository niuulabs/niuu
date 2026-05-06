import { z } from 'zod';

/**
 * Tracker browser domain types.
 *
 * The tracker browser lets operators browse projects, milestones, and issues
 * from an external issue tracker (Linear, GitHub Projects, etc.) and import
 * them as Sagas.
 *
 * Owner: plugin-tyr.
 */

export const trackerProjectSchema = z.object({
  id: z.string().min(1),
  name: z.string().min(1),
  description: z.string(),
  status: z.string(),
  url: z.string(),
  milestoneCount: z.number().int().nonnegative(),
  issueCount: z.number().int().nonnegative(),
  slug: z.string().default(''),
});
export type TrackerProject = z.infer<typeof trackerProjectSchema>;

export const trackerMilestoneSchema = z.object({
  id: z.string().min(1),
  projectId: z.string().min(1),
  name: z.string().min(1),
  description: z.string(),
  sortOrder: z.number().int().nonnegative(),
  progress: z.number().min(0).max(100),
});
export type TrackerMilestone = z.infer<typeof trackerMilestoneSchema>;

export const trackerIssueSchema = z.object({
  id: z.string().min(1),
  identifier: z.string().min(1),
  title: z.string().min(1),
  description: z.string(),
  status: z.string(),
  assignee: z.string().nullable(),
  labels: z.array(z.string()),
  priority: z.number().int().nonnegative(),
  url: z.string(),
  milestoneId: z.string().nullable(),
});
export type TrackerIssue = z.infer<typeof trackerIssueSchema>;

export const repoInfoSchema = z.object({
  provider: z.string(),
  org: z.string(),
  name: z.string(),
  cloneUrl: z.string(),
  url: z.string(),
  defaultBranch: z.string(),
  branches: z.array(z.string()),
});
export type RepoInfo = z.infer<typeof repoInfoSchema>;
