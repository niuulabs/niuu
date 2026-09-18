import type { ChatMessage, ChatMessagePart } from '../types';
import { mergeCanonicalParts } from './canonicalTextRepair';
import { publicTextContent } from './orderedPublicText';

function sameScope(a: ChatMessagePart, b: ChatMessagePart): boolean {
  return (
    (!a.turn_id || !b.turn_id || a.turn_id === b.turn_id) &&
    (!a.thread_id || !b.thread_id || a.thread_id === b.thread_id)
  );
}

/** Bind REST rows to observed native turns, never by text, time or the running status alone. */
function sameTurn(canonical: ChatMessage, live: ChatMessage): boolean {
  if (canonical.role !== live.role) return false;
  if (canonical.id === live.id) return true;
  return Boolean(
    canonical.parts?.some((a) =>
      live.parts?.some(
        (b) =>
          sameScope(a, b) &&
          ((a.turn_id && a.turn_id === b.turn_id) ||
            (a.type === b.type &&
              ((a.id && a.id === b.id) || (a.tool_use_id && a.tool_use_id === b.tool_use_id)))),
      ),
    ),
  );
}

/** Recover the whole observed turn prefix even if the socket won the history GET. */
export function reconcileHistoryMessages(
  canonical: ChatMessage[],
  observed: ChatMessage[],
): { recent: ChatMessage[]; current: ChatMessage[] } {
  const claimed = new Set<string>();
  const recent = canonical.map((message) => {
    const matches = observed.filter((live) => !claimed.has(live.id) && sameTurn(message, live));
    if (!matches.length) return message;
    // A preview has no complete part sequence to merge. Keep its explicit history
    // reader AND the live tail instead of silently dropping either one.
    if (message.historyPreview) return message;
    let parts = message.parts ? [...message.parts] : undefined;
    for (const live of matches) {
      claimed.add(live.id);
      if (!live.parts?.length) continue;
      parts = mergeCanonicalParts(parts ?? [], live.parts, (saved, current) =>
        saved.type === 'text' && saved.complete && !current.complete ? saved : current,
      );
    }
    const live = matches.at(-1)!;
    return {
      ...message,
      // Keep the socket identity: pending stream updates still address this row.
      id: live.id,
      parts,
      content: parts?.some((part) => part.type === 'text')
        ? publicTextContent(parts)
        : live.content || message.content,
      status: live.status,
      participant: live.participant ?? message.participant,
      metadata: live.metadata ?? message.metadata,
    };
  });
  const retained = new Set(recent.map((message) => message.id));
  return {
    recent,
    current: observed.filter((message) => !claimed.has(message.id) || retained.has(message.id)),
  };
}
