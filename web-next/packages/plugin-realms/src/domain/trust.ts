import { ACTION_CLASSES, type ActionClass } from './realm';

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
