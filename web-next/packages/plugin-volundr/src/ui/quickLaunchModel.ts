import type { BifrostModel } from '@niuulabs/plugin-bifrost';
import type { VolundrTarget } from '../models/volundr.model';

/** The same two curated standards as Lexi iOS's NewForgeSessionView. */
export const FORGE_STANDARDS = [
  {
    id: 'claude',
    name: 'Claude',
    harness: 'Claude Code · interactive tmux',
    definition: 'skuldClaudeInteractive',
    models: [
      { id: 'claude-fable-5-1', name: 'Claude Fable 5.1' },
      { id: 'claude-opus-5-5', name: 'Claude Opus 5.5' },
    ],
  },
  {
    id: 'codex',
    name: 'Codex',
    harness: 'OpenAI Codex · Skuld CLI',
    definition: 'skuldCodex',
    models: [
      { id: 'gpt-6-astra', name: 'GPT-6 Astra' },
      { id: 'gpt-6-sol', name: 'GPT-6 Sol' },
    ],
  },
] as const;
export type ForgeStandardId = (typeof FORGE_STANDARDS)[number]['id'];
export const EFFORT_LABELS: Record<string, string> = {
  off: 'Off',
  none: 'None',
  minimal: 'Minimal',
  low: 'Low',
  medium: 'Medium',
  high: 'High',
  xhigh: 'Extra High',
  max: 'Max',
  ultra: 'Ultra',
};
export function selectedEffort(model: BifrostModel | undefined, preferred: string): string {
  const levels = model?.effortLevels ?? [];
  if (levels.includes(preferred)) return preferred;
  if (model?.defaultEffort && levels.includes(model.defaultEffort)) return model.defaultEffort;
  return levels.at(-1) ?? '';
}
export function hostDefaultFolder(host: VolundrTarget | undefined): string {
  const value = host?.config?.defaultFolder;
  return typeof value === 'string' ? value : '';
}
export function forgeErrorMessage(error: unknown): string {
  if (error && typeof error === 'object' && 'detail' in error && typeof error.detail === 'string')
    return error.detail;
  return error instanceof Error ? error.message : 'The Forge request failed. Please try again.';
}
