import type { DecisionRecord } from '@niuulabs/plugin-valkyrie';
import { ACTION_CLASSES, type ActionClass } from './realm';

/** Decision summaries arrive as "Valkyrie <name> in <env> recommends: …"; keep the verb phrase. */
const DECISION_PREFIX = /^valkyrie\s+\S+\s+in\s+\S+\s+(?:recommends:\s*|judged\s+)/i;

export function decisionLine(decision: Pick<DecisionRecord, 'summary' | 'recommendedAction'>): string {
  const raw = (decision.summary || decision.recommendedAction || '').trim();
  const stripped = raw.replace(DECISION_PREFIX, '').trim();
  if (!stripped) return raw;
  return stripped.charAt(0).toUpperCase() + stripped.slice(1);
}

/** Dot colour for a timeline row, by what happened to the decision. */
export type ActivityTone = 'brand' | 'ok' | 'warn' | 'muted';

export function decisionTone(decision: Pick<DecisionRecord, 'outcome' | 'actionAuthority'>): ActivityTone {
  const outcome = decision.outcome.toLowerCase();
  if (outcome.includes('fail') || outcome.includes('reject') || outcome.includes('error')) return 'warn';
  if (outcome.includes('pending') || outcome.includes('review') || outcome.includes('ask')) return 'warn';
  if (outcome.includes('observ') || outcome.includes('none') || outcome === '') return 'muted';
  return decision.actionAuthority === 'autonomous' ? 'ok' : 'brand';
}

/** Trust levels as one sentence: what runs alone, what asks, what never happens. */
export function trustSentence(levels: Partial<Record<ActionClass, number | null>>): string {
  const alone: string[] = [];
  const asks: string[] = [];
  const never: string[] = [];
  for (const actionClass of ACTION_CLASSES) {
    if (actionClass === 'observe') continue;
    const level = levels[actionClass] ?? null;
    if (level === null) never.push(actionClass);
    else if (level >= 2) alone.push(actionClass);
    else asks.push(actionClass);
  }
  const parts: string[] = [];
  if (alone.length) parts.push(`${list(alone)} on its own`);
  if (asks.length) parts.push(`asks before ${list(asks)}`);
  if (never.length) parts.push(`never ${list(never)}`);
  return parts.length ? capitalize(parts.join(' · ')) : 'No trust granted yet';
}

function list(items: string[]): string {
  if (items.length <= 1) return items.join('');
  return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;
}

function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
