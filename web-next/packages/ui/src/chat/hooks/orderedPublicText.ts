import type { ChatMessagePart } from '../types';

export interface PublicTextEvent {
  type: string;
  item_id?: string;
  index?: number;
  phase?: string;
  turn_id?: string;
  thread_id?: string;
  content_block?: Record<string, unknown>;
  complete?: boolean;
  text_bytes?: number;
  text_sha256?: string;
  delta?: { type?: string; text?: string };
  message?: { content?: Array<Record<string, unknown>> };
}

/** Fold identified public text at its first-seen anchor. Legacy unkeyed events use the existing path. */
export function foldPublicText(
  parts: readonly ChatMessagePart[],
  event: PublicTextEvent,
): ChatMessagePart[] | null {
  const content = event.message?.content;
  if (
    event.type === 'assistant' &&
    content?.some((block) => block.type !== 'text') &&
    content.some((block) => block.type === 'text' && typeof block.id === 'string' && block.id) &&
    content.every((block) => ['text', 'tool_use', 'tool_result'].includes(String(block.type)))
  ) {
    let next = [...parts];
    for (const block of content) {
      if (block.type === 'text') {
        const folded = foldPublicText(next, { ...event, message: { content: [block] } });
        if (!folded) return null;
        next = folded;
      } else if (block.type === 'tool_use' && typeof block.id === 'string') {
        next = upsertToolPart(next, {
          type: 'tool_use',
          id: block.id,
          name: typeof block.name === 'string' ? block.name : '',
          input:
            typeof block.input === 'object' && block.input !== null
              ? (block.input as Record<string, unknown>)
              : {},
          turn_id: event.turn_id,
          thread_id: event.thread_id,
        });
      } else if (block.type === 'tool_result' && typeof block.tool_use_id === 'string') {
        next = upsertToolPart(next, {
          type: 'tool_result',
          tool_use_id: block.tool_use_id,
          content:
            typeof block.content === 'string' ? block.content : JSON.stringify(block.content ?? ''),
        });
      } else return null;
    }
    return next;
  }
  let blocks: Array<Record<string, unknown>>;
  if (event.type === 'content_block_start' && event.content_block?.type === 'text') {
    blocks = [event.content_block];
  } else if (event.type === 'content_block_delta' && event.delta?.type === 'text_delta') {
    blocks = [{ type: 'text', id: event.item_id, text: event.delta.text ?? '' }];
  } else if (event.type === 'content_block_stop' && event.item_id && event.complete === true) {
    blocks = [{ type: 'text', id: event.item_id }];
  } else if (
    event.type === 'assistant' &&
    event.message?.content?.length &&
    event.message.content.every((block) => block.type === 'text')
  ) {
    blocks = event.message.content;
  } else {
    return null;
  }
  if (
    blocks.some(
      (block) => typeof (block.id ?? event.item_id) !== 'string' || !(block.id ?? event.item_id),
    )
  )
    return null;

  const next = [...parts];
  for (const block of blocks) {
    const id = (block.id ?? event.item_id) as string;
    const turn = typeof block.turn_id === 'string' ? block.turn_id : event.turn_id;
    const thread = typeof block.thread_id === 'string' ? block.thread_id : event.thread_id;
    const index = next.findIndex(
      (part) =>
        part.type === 'text' &&
        part.id === id &&
        (!turn || !part.turn_id || turn === part.turn_id) &&
        (!thread || !part.thread_id || thread === part.thread_id),
    );
    const previous = index >= 0 ? next[index] : undefined;
    const isDelta = event.type === 'content_block_delta';
    const isStop = event.type === 'content_block_stop';
    if (isStop && !previous) continue;
    const isComplete = event.type === 'assistant' || isStop;
    // An authoritative completion wins over a late token or duplicated start for that same item.
    if (previous?.complete && !isComplete) continue;
    const text = typeof block.text === 'string' ? block.text : '';
    const part: ChatMessagePart = {
      ...previous,
      type: 'text',
      id,
      text: isDelta
        ? (previous?.text ?? '') + text
        : isComplete && !isStop
          ? text
          : (previous?.text ?? text),
      phase: typeof block.phase === 'string' ? block.phase : (event.phase ?? previous?.phase),
      turn_id: turn ?? previous?.turn_id,
      thread_id: thread ?? previous?.thread_id,
      index: typeof block.index === 'number' ? block.index : (event.index ?? previous?.index),
      complete: isComplete || previous?.complete === true,
      ...(typeof block.id_source === 'string' ? { id_source: block.id_source } : {}),
    };
    if (index < 0) next.push(part);
    else next[index] = part;
  }
  return next;
}

/** Compatibility aggregate joins messages, never arbitrary delta chunks. */
export function publicTextContent(parts: readonly ChatMessagePart[]): string {
  return parts
    .filter((part) => part.type === 'text' && part.text)
    .map((part) => part.text ?? '')
    .join('\n\n');
}

export function upsertToolPart(
  parts: readonly ChatMessagePart[],
  part: ChatMessagePart,
): ChatMessagePart[] {
  const index = parts.findIndex(
    (existing) =>
      existing.type === part.type &&
      (part.type === 'tool_result'
        ? existing.tool_use_id === part.tool_use_id
        : existing.id === part.id),
  );
  if (index < 0) return [...parts, part];
  const next = [...parts];
  const previous = next[index];
  const input =
    part.type === 'tool_use' && part.input && Object.keys(part.input).length === 0
      ? previous?.input
      : part.input;
  next[index] = { ...previous, ...part, ...(part.type === 'tool_use' ? { input } : {}) };
  return next;
}

/** A stop receipt validates captured bytes; it never supplies missing prose. */
export async function validateTextReceipt(text: string, event: PublicTextEvent): Promise<boolean> {
  const bytes = new TextEncoder().encode(text);
  if (event.text_bytes !== undefined && bytes.byteLength !== event.text_bytes) return false;
  if (!event.text_sha256) return true;
  if (!globalThis.crypto?.subtle) return false;
  try {
    const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes);
    const actual = Array.from(new Uint8Array(digest), (byte) =>
      byte.toString(16).padStart(2, '0'),
    ).join('');
    return actual === event.text_sha256.toLowerCase();
  } catch {
    return false;
  }
}
