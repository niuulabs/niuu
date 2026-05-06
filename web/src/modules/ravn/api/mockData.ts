/**
 * Mock data for Ravn UI demo / fallback.
 *
 * Based on the actual built-in persona YAML definitions in src/ravn/personas/.
 * Used when the backend API is unavailable or returns empty results.
 */

import type { PersonaSummary } from './types';

// ---------------------------------------------------------------------------
// Personas — mirrors the 21 built-in YAML files
// ---------------------------------------------------------------------------

export const MOCK_PERSONAS: PersonaSummary[] = [
  {
    name: 'architect',
    permissionMode: 'read-only',
    allowedTools: ['file', 'web', 'mimir', 'ravn'],
    iterationBudget: 25,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: '',
    consumesEvents: [],
  },
  {
    name: 'autonomous-agent',
    permissionMode: 'full-access',
    allowedTools: [],
    iterationBudget: 100,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: '',
    consumesEvents: [],
  },
  {
    name: 'coder',
    permissionMode: 'workspace_write',
    allowedTools: ['file', 'git', 'terminal', 'ravn'],
    iterationBudget: 40,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: 'code.changed',
    consumesEvents: [
      'review.changes_requested',
      'security.changes_requested',
      'code.requested',
      'bug.fix.requested',
      'feature.requested',
    ],
  },
  {
    name: 'coding-agent',
    permissionMode: 'workspace-write',
    allowedTools: ['mimir_query', 'file', 'git', 'terminal', 'web', 'todo', 'ravn'],
    iterationBudget: 40,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: 'code.changed',
    consumesEvents: ['code.requested', 'bug.fix.requested', 'feature.requested'],
  },
  {
    name: 'coordinator',
    permissionMode: 'workspace-write',
    allowedTools: ['cascade', 'file', 'ravn', 'todo'],
    iterationBudget: 30,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: '',
    consumesEvents: [],
  },
  {
    name: 'draft-a-note',
    permissionMode: 'read-only',
    allowedTools: ['file', 'web', 'mimir', 'ravn'],
    iterationBudget: 15,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: '',
    consumesEvents: [],
  },
  {
    name: 'health-auditor',
    permissionMode: 'read-only',
    allowedTools: ['file', 'terminal', 'web', 'ravn'],
    iterationBudget: 20,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: 'health.completed',
    consumesEvents: ['health.check.requested', 'cron.hourly'],
  },
  {
    name: 'investigator',
    permissionMode: 'workspace-write',
    allowedTools: ['file', 'git', 'terminal', 'web', 'ravn'],
    iterationBudget: 40,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: 'investigation.completed',
    consumesEvents: ['bug.reported', 'incident.opened', 'qa.failed'],
  },
  {
    name: 'mimir-curator',
    permissionMode: 'read-only',
    allowedTools: ['file', 'mimir', 'ravn'],
    iterationBudget: 20,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: '',
    consumesEvents: [],
  },
  {
    name: 'office-hours',
    permissionMode: 'read-only',
    allowedTools: ['file', 'web', 'mimir', 'ravn'],
    iterationBudget: 20,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: '',
    consumesEvents: [],
  },
  {
    name: 'planning-agent',
    permissionMode: 'read-only',
    allowedTools: ['file', 'web', 'mimir', 'ravn', 'todo'],
    iterationBudget: 25,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: '',
    consumesEvents: [],
  },
  {
    name: 'produce-recap',
    permissionMode: 'read-only',
    allowedTools: ['file', 'mimir', 'ravn'],
    iterationBudget: 15,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: '',
    consumesEvents: [],
  },
  {
    name: 'qa-agent',
    permissionMode: 'workspace-write',
    allowedTools: ['file', 'git', 'terminal', 'ravn'],
    iterationBudget: 30,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: 'qa.completed',
    consumesEvents: ['review.completed', 'test.requested'],
  },
  {
    name: 'research-agent',
    permissionMode: 'read-only',
    allowedTools: ['mimir_query', 'web', 'file', 'ravn'],
    iterationBudget: 30,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: '',
    consumesEvents: [],
  },
  {
    name: 'research-and-distill',
    permissionMode: 'read-only',
    allowedTools: ['file', 'web', 'mimir', 'ravn'],
    iterationBudget: 25,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: '',
    consumesEvents: [],
  },
  {
    name: 'retro-analyst',
    permissionMode: 'read-only',
    allowedTools: ['file', 'mimir', 'ravn'],
    iterationBudget: 20,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: '',
    consumesEvents: [],
  },
  {
    name: 'reviewer',
    permissionMode: 'read-only',
    allowedTools: ['file', 'git', 'web', 'ravn'],
    iterationBudget: 25,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: 'review.completed',
    consumesEvents: ['code.changed', 'review.requested'],
  },
  {
    name: 'security',
    permissionMode: 'read_only',
    allowedTools: ['file', 'git', 'ravn'],
    iterationBudget: 60,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: 'security.completed',
    consumesEvents: ['code.changed'],
  },
  {
    name: 'security-auditor',
    permissionMode: 'read-only',
    allowedTools: ['file', 'terminal', 'web', 'ravn'],
    iterationBudget: 30,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: '',
    consumesEvents: [],
  },
  {
    name: 'ship-agent',
    permissionMode: 'workspace-write',
    allowedTools: ['file', 'git', 'terminal', 'web', 'ravn'],
    iterationBudget: 15,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: 'ship.completed',
    consumesEvents: ['qa.completed', 'ship.requested'],
  },
  {
    name: 'verifier',
    permissionMode: 'workspace-write',
    allowedTools: ['file', 'git', 'terminal', 'web', 'ravn'],
    iterationBudget: 30,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: 'verification.completed',
    consumesEvents: ['qa.completed', 'code.changed', 'verification.requested'],
  },
];

// ---------------------------------------------------------------------------
// Sessions — realistic demo sessions using actual personas
// ---------------------------------------------------------------------------

export interface MockSession {
  id: string;
  status: string;
  model: string;
  created_at: string;
  persona: string;
}

export const MOCK_SESSIONS: MockSession[] = [
  {
    id: 'a3f1b2c4-8e7d-4a6f-9b0c-1d2e3f4a5b6c',
    status: 'running',
    model: 'claude-sonnet-4-6',
    created_at: '2026-04-15T09:12:34Z',
    persona: 'coding-agent',
  },
  {
    id: 'b7e2c9d1-3a4f-4b8e-a1c6-5d7f8e9a0b2c',
    status: 'running',
    model: 'claude-opus-4-6',
    created_at: '2026-04-15T08:45:11Z',
    persona: 'reviewer',
  },
  {
    id: 'c4d5e6f7-1a2b-4c3d-8e9f-0a1b2c3d4e5f',
    status: 'idle',
    model: 'claude-haiku-4-5',
    created_at: '2026-04-15T08:30:00Z',
    persona: 'security',
  },
  {
    id: 'd8e9f0a1-2b3c-4d5e-6f7a-8b9c0d1e2f3a',
    status: 'running',
    model: 'claude-sonnet-4-6',
    created_at: '2026-04-15T07:55:22Z',
    persona: 'qa-agent',
  },
  {
    id: 'e1f2a3b4-5c6d-4e7f-8a9b-0c1d2e3f4a5b',
    status: 'stopped',
    model: 'claude-opus-4-6',
    created_at: '2026-04-14T22:10:45Z',
    persona: 'investigator',
  },
  {
    id: 'f5a6b7c8-9d0e-4f1a-2b3c-4d5e6f7a8b9c',
    status: 'idle',
    model: 'claude-sonnet-4-6',
    created_at: '2026-04-14T18:33:07Z',
    persona: 'health-auditor',
  },
];
