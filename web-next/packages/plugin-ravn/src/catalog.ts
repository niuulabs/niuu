/**
 * Seed EventCatalog and ToolRegistry constants.
 *
 * These are the canonical lists for the Ravn plugin.
 * EventCatalog grows as personas are created via the PersonasPage.
 * ToolRegistry mirrors the backend TOOL_REGISTRY in src/ravn/tools/.
 */

import type { EventCatalog, ToolRegistry, PersonaRole } from '@niuulabs/domain';

export const PERSONA_ROLE_ORDER: PersonaRole[] = [
  'arbiter',
  'autonomy',
  'build',
  'coord',
  'investigate',
  'knowledge',
  'observe',
  'plan',
  'qa',
  'report',
  'review',
  'write',
  // Legacy roles — kept for backward compatibility with Tyr / Observatory
  'audit',
  'gate',
  'index',
  'ship',
  'verify',
];

export const SEED_EVENT_CATALOG: EventCatalog = [
  { name: 'code.changed', schema: { file: 'string', diff: 'string' } },
  { name: 'code.requested', schema: { description: 'string' } },
  { name: 'bug.fix.requested', schema: { issue: 'string', severity: 'string' } },
  { name: 'bug.reported', schema: { title: 'string', stack: 'string' } },
  { name: 'feature.requested', schema: { title: 'string', spec: 'string' } },
  { name: 'review.requested', schema: { pr: 'string' } },
  { name: 'review.completed', schema: { pr: 'string', outcome: 'string' } },
  { name: 'review.changes_requested', schema: { pr: 'string', comments: 'array' } },
  { name: 'security.changes_requested', schema: { pr: 'string', findings: 'array' } },
  { name: 'security.completed', schema: { pr: 'string', passed: 'boolean' } },
  { name: 'qa.completed', schema: { suite: 'string', passed: 'boolean', failures: 'number' } },
  { name: 'qa.failed', schema: { suite: 'string', failures: 'array' } },
  { name: 'test.requested', schema: { scope: 'string' } },
  { name: 'ship.completed', schema: { version: 'string', env: 'string' } },
  { name: 'ship.requested', schema: { version: 'string', env: 'string' } },
  { name: 'verification.completed', schema: { scope: 'string', passed: 'boolean' } },
  { name: 'verification.requested', schema: { scope: 'string' } },
  { name: 'investigation.completed', schema: { issue: 'string', findings: 'string' } },
  {
    name: 'research.requested',
    schema: {
      question: 'string',
      slug: 'string',
      max_sources: 'number',
      constraints: 'string',
    },
  },
  {
    name: 'research.completed',
    schema: { summary: 'string', page_path: 'string' },
  },
  { name: 'incident.opened', schema: { id: 'string', severity: 'string' } },
  { name: 'health.completed', schema: { status: 'string', metrics: 'object' } },
  { name: 'health.check.requested', schema: {} },
  { name: 'cron.hourly', schema: { ts: 'string' } },
  { name: 'plan.completed', schema: { tasks: 'array' } },
];

export const SEED_TOOL_REGISTRY: ToolRegistry = [
  { id: 'read', group: 'fs', destructive: false, desc: 'Read a file from disk' },
  { id: 'write', group: 'fs', destructive: true, desc: 'Write or overwrite a file' },
  { id: 'list', group: 'fs', destructive: false, desc: 'List directory contents' },
  { id: 'bash', group: 'shell', destructive: true, desc: 'Run a shell command' },
  { id: 'git.status', group: 'git', destructive: false, desc: 'Show working tree status' },
  { id: 'git.log', group: 'git', destructive: false, desc: 'Show commit history' },
  { id: 'git.diff', group: 'git', destructive: false, desc: 'Show diffs' },
  { id: 'git.checkout', group: 'git', destructive: true, desc: 'Switch branches or restore files' },
  { id: 'git.push', group: 'git', destructive: true, desc: 'Push commits to remote' },
  { id: 'mimir.read', group: 'mimir', destructive: false, desc: 'Query Mímir knowledge store' },
  { id: 'mimir.write', group: 'mimir', destructive: false, desc: 'Write to Mímir knowledge store' },
  { id: 'mimir.delete', group: 'mimir', destructive: true, desc: 'Delete entries from Mímir' },
  {
    id: 'observe.metrics',
    group: 'observe',
    destructive: false,
    desc: 'Read observability metrics',
  },
  { id: 'observe.logs', group: 'observe', destructive: false, desc: 'Read log streams' },
  { id: 'security.scan', group: 'security', destructive: false, desc: 'Static security scanner' },
  { id: 'security.secrets', group: 'security', destructive: false, desc: 'Secret detection scan' },
  { id: 'bus.emit', group: 'bus', destructive: false, desc: 'Emit an event to the event bus' },
  { id: 'ravn.dispatch', group: 'bus', destructive: false, desc: 'Dispatch a ravn session' },
  { id: 'ravn.cascade', group: 'bus', destructive: false, desc: 'Cascade to child ravens' },
  { id: 'web', group: 'observe', destructive: false, desc: 'Fetch a URL or search the web' },
];
