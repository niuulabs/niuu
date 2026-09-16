import type { ChatMessagePart } from '../types';

export interface TextRepairIdentity {
  item_id: string;
  turn_id?: string;
  thread_id?: string;
}

function compatibleScope(a: ChatMessagePart, b: TextRepairIdentity): boolean {
  return (
    (!a.turn_id || !b.turn_id || a.turn_id === b.turn_id) &&
    (!a.thread_id || !b.thread_id || a.thread_id === b.thread_id)
  );
}

function matchesTarget(part: ChatMessagePart, target: TextRepairIdentity): boolean {
  return part.type === 'text' && part.id === target.item_id && compatibleScope(part, target);
}

function sameAnchor(a: ChatMessagePart, b: ChatMessagePart): boolean {
  if (a.type !== b.type) return false;
  if (a.type === 'text' && (a.id || b.id)) {
    return Boolean(a.id && a.id === b.id && compatibleScope(a, { ...b, item_id: b.id! }));
  }
  if (a.type === 'tool_use') return Boolean(a.id && a.id === b.id);
  if (a.type === 'tool_result') return Boolean(a.tool_use_id && a.tool_use_id === b.tool_use_id);
  // Unidentified legacy prose/reasoning has no invented identity. Match equal occurrences
  // one-to-one, so legitimately repeated prose is never collapsed into a single anchor.
  return a.text === b.text;
}

function anchorBucket(part: ChatMessagePart): string {
  if (part.type === 'tool_result') return JSON.stringify([part.type, part.tool_use_id]);
  if (part.id) return JSON.stringify([part.type, part.id]);
  return JSON.stringify([part.type, part.text]);
}

/**
 * Repair one native text item without replacing a newer live turn with an older GET.
 * Server anchors supply chronology; matching live objects preserve newer text/tool state.
 * Unmatched live parts stay before their next known anchor, or at the live tail.
 * The caller must bind this canonical turn to the same current assistant turn.
 */
export function repairCanonicalText(
  canonical: readonly ChatMessagePart[],
  current: readonly ChatMessagePart[],
  target: TextRepairIdentity,
): ChatMessagePart[] | null {
  const targets = canonical.filter((part) => matchesTarget(part, target));
  const authoritative = targets[0];
  if (
    !target.item_id ||
    targets.length !== 1 ||
    authoritative?.complete !== true ||
    typeof authoritative.text !== 'string'
  )
    return null;

  const claimed = new Set<number>();
  const buckets = new Map<string, { indexes: number[]; cursor: number }>();
  current.forEach((part, index) => {
    const key = anchorBucket(part);
    let bucket = buckets.get(key);
    if (!bucket) {
      bucket = { indexes: [], cursor: 0 };
      buckets.set(key, bucket);
    }
    bucket.indexes.push(index);
  });
  const matches = canonical.map((part) => {
    const bucket = buckets.get(anchorBucket(part));
    if (!bucket) return -1;
    // Normally an identified bucket contains one item. Legacy repeated text is a queue,
    // making large ordinary turns linear instead of scanning every live part per anchor.
    for (let at = bucket.cursor; at < bucket.indexes.length; at++) {
      const index = bucket.indexes[at]!;
      if (claimed.has(index) || !sameAnchor(current[index]!, part)) continue;
      claimed.add(index);
      while (bucket.cursor < bucket.indexes.length && claimed.has(bucket.indexes[bucket.cursor]!))
        bucket.cursor++;
      return index;
    }
    return -1;
  });
  const next: ChatMessagePart[] = [];
  let cursor = 0;
  for (const [at, part] of canonical.entries()) {
    const matched = matches[at]!;
    if (matched >= 0) {
      while (cursor < matched) {
        if (!claimed.has(cursor)) next.push(current[cursor]!);
        cursor++;
      }
      cursor = Math.max(cursor, matched + 1);
      const live = current[matched]!;
      // A whole native completion may have arrived while the GET was suspended.
      // It must win over this older snapshot, including a same-length correction.
      next.push(matchesTarget(part, target) && live.complete !== true ? part : live);
    } else {
      next.push(part);
    }
  }
  while (cursor < current.length) {
    if (!claimed.has(cursor)) next.push(current[cursor]!);
    cursor++;
  }
  return next;
}
