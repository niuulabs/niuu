import { getAuthHeaders } from '@niuulabs/query';
import { normalizeSessionUrl, wsUrlToHttpBase } from '../transport';
import type { ConversationTurn } from './useSkuldChat';

export const HISTORY_PAGE_SIZE = 50;
export const HISTORY_PAGE_BYTES = 256 * 1024;
// Retained Forge facades append routing/timing metadata after fitting the page.
// Reserve envelope space without relaxing the browser's hard transfer bound.
export const HISTORY_REQUEST_BYTES = HISTORY_PAGE_BYTES - 4 * 1024;
const LEGACY_SEAM_ATTEMPTS = 3;

export interface HistoryPage {
  turns: ConversationTurn[];
  projection_revision?: string;
  history_protocol?: number;
  head_seq?: number;
  total_turns: number;
  window_offset: number;
  window_end: number;
  older_cursor?: string | null;
}

export class HistoryChangedError extends Error {
  constructor() {
    super('Conversation history changed. Reload the recent messages to continue.');
  }
}

/** Preserve the host/proxy prefix. The Forge facade also pages retained old gateways. */
export function conversationUrl(socketUrl: string): URL {
  const url = new URL(normalizeSessionUrl(socketUrl) ?? socketUrl);
  url.protocol = url.protocol === 'wss:' ? 'https:' : 'http:';
  const match = url.pathname.match(/^(.*)\/s\/([^/]+)\/(?:api\/)?session$/);
  url.pathname = match
    ? `${match[1]}/api/v1/forge/sessions/${match[2]}/conversation`
    : `${new URL(wsUrlToHttpBase(socketUrl)!).pathname.replace(/\/$/, '')}/api/conversation/history`;
  url.search = '';
  return url;
}

export function historySocketUrl(socketUrl: string | null, enabled: boolean): string | null {
  if (!socketUrl || !enabled) return socketUrl;
  try {
    const url = new URL(normalizeSessionUrl(socketUrl) ?? socketUrl);
    url.searchParams.set('history', 'recent');
    url.searchParams.set('history_protocol', '2');
    url.searchParams.set('history_delivery', 'none');
    return url.href;
  } catch {
    return socketUrl;
  }
}

export function isHistoryRecovery(event: {
  type: string;
  code?: string;
  error?: string | { code?: string };
}): boolean {
  const code = event.code ?? (typeof event.error === 'object' ? event.error?.code : undefined);
  return (
    event.type === 'history_gap' ||
    (event.type === 'error' && code === 'conversation_history_too_large')
  );
}

/** Stop reading an old peer that ignores the requested budget, before parsing its transcript. */
async function boundedJson(response: Response): Promise<unknown> {
  if (!response.body) return response.json();
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > HISTORY_PAGE_BYTES) {
        await reader.cancel();
        throw new Error('This Forge did not honor the history page size. Update its history API.');
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  return JSON.parse(new TextDecoder().decode(bytes));
}

async function pageRequest(url: URL, signal: AbortSignal): Promise<HistoryPage> {
  const response = await fetch(url.href, { headers: getAuthHeaders(), signal });
  if (!response.ok) {
    if (response.status === 409) {
      const error = await response.json();
      if (error?.detail?.code === 'history_cursor_invalid') throw new HistoryChangedError();
    }
    throw new Error(`History request failed (HTTP ${response.status}).`);
  }
  const data = (await boundedJson(response)) as HistoryPage;
  const limit = Number(url.searchParams.get('limit'));
  if (
    !Array.isArray(data.turns) ||
    data.turns.length > limit ||
    data.turns.some((turn) => typeof turn.id !== 'string' || !turn.id) ||
    new Set(data.turns.map((turn) => turn.id)).size !== data.turns.length
  ) {
    throw new Error('The Forge returned an invalid history page.');
  }
  // Old standalone brokers can return their complete SMALL transcript without page metadata.
  // Large ignored requests are rejected by the byte/count guards above.
  const start = data.window_offset ?? 0;
  const total = data.total_turns ?? data.turns.length;
  const end = data.window_end ?? start + data.turns.length;
  if (
    (data.history_protocol !== undefined && data.history_protocol !== 2) ||
    (data.history_protocol === 2 &&
      ![data.window_offset, data.window_end, data.total_turns].every(Number.isSafeInteger)) ||
    ![start, total, end].every(Number.isSafeInteger) ||
    start < 0 ||
    end !== start + data.turns.length ||
    end > total ||
    (data.history_protocol === 2 && start > 0 && !data.older_cursor) ||
    (url.searchParams.has('cursor') && data.history_protocol !== 2)
  ) {
    throw new Error('The Forge returned an invalid history boundary.');
  }
  return { ...data, total_turns: total, window_offset: start, window_end: end };
}

function requestUrl(socketUrl: string, limit: number): URL {
  const url = conversationUrl(socketUrl);
  url.searchParams.set('history_protocol', '2');
  url.searchParams.set('detail', 'shallow');
  url.searchParams.set('limit', String(limit));
  url.searchParams.set('max_bytes', String(HISTORY_REQUEST_BYTES));
  return url;
}

async function olderPage(
  socketUrl: string,
  boundary: HistoryPage,
  limit: number,
  signal: AbortSignal,
): Promise<HistoryPage> {
  const url = requestUrl(socketUrl, limit);
  if (boundary.history_protocol === 2) {
    url.searchParams.set('cursor', boundary.older_cursor!);
    const page = await pageRequest(url, signal);
    if (
      page.window_end !== boundary.window_offset ||
      page.projection_revision !== boundary.projection_revision
    )
      throw new HistoryChangedError();
    return page;
  }
  // Legacy before is relative to a moving tail. Include the held seam and check its ID/index.
  url.searchParams.delete('history_protocol');
  url.searchParams.set('limit', String(limit + 1));
  let total = boundary.total_turns;
  for (let attempt = 0; attempt < LEGACY_SEAM_ATTEMPTS; attempt++) {
    url.searchParams.set('before', String(Math.max(0, total - boundary.window_offset - 1)));
    const page = await pageRequest(url, signal);
    if (page.projection_revision !== boundary.projection_revision) throw new HistoryChangedError();
    const seam = boundary.window_offset - page.window_offset;
    if (page.turns[seam]?.id === boundary.turns[0]?.id && seam > 0) {
      return { ...page, turns: page.turns.slice(0, seam), window_end: boundary.window_offset };
    }
    if (seam === 0 && page.turns[0]?.id === boundary.turns[0]?.id) {
      // A huge seam consumed the byte budget: it has been verified separately. Fetch before it.
      url.searchParams.set('limit', String(limit));
      url.searchParams.set('before', String(page.total_turns - boundary.window_offset));
      const older = await pageRequest(url, signal);
      if (
        older.total_turns === page.total_turns &&
        older.window_end === boundary.window_offset &&
        older.projection_revision === page.projection_revision
      )
        return older;
      total = older.total_turns;
      url.searchParams.set('limit', String(limit + 1));
      continue;
    }
    if (page.total_turns === total) throw new HistoryChangedError();
    total = page.total_turns;
  }
  throw new HistoryChangedError();
}

/** A visual batch is 50 rows; servers may split it into smaller byte-bounded pages. */
export async function fetchHistoryBatch(
  socketUrl: string,
  signal: AbortSignal,
  before?: HistoryPage,
): Promise<HistoryPage> {
  let page = before
    ? await olderPage(socketUrl, before, HISTORY_PAGE_SIZE, signal)
    : await pageRequest(requestUrl(socketUrl, HISTORY_PAGE_SIZE), signal);
  let turns = page.turns;
  const newest = page;
  while (turns.length < HISTORY_PAGE_SIZE && page.window_offset > 0) {
    const older = await olderPage(socketUrl, page, HISTORY_PAGE_SIZE - turns.length, signal);
    if (!older.turns.length || older.window_offset >= page.window_offset)
      throw new HistoryChangedError();
    turns = [...older.turns, ...turns];
    page = older;
  }
  // Page budgets are a transport detail, not a different kind of message.
  // Resolve truncated rows before they enter the normal history/live merge.
  const complete: ConversationTurn[] = [];
  for (const turn of turns) {
    signal.throwIfAborted();
    complete.push(turn.history_preview ? await fetchHistoryItem(socketUrl, turn.id, signal) : turn);
  }
  signal.throwIfAborted();
  return { ...page, turns: complete, window_end: newest.window_end, head_seq: newest.head_seq };
}

export async function fetchHistoryItem(
  socketUrl: string,
  id: string,
  signal: AbortSignal,
): Promise<ConversationTurn> {
  const url = conversationUrl(socketUrl);
  // Load all prose for this identity; large tool bodies keep their normal lazy reads.
  url.searchParams.set('turn_id', id);
  url.searchParams.set('detail', 'shallow');
  const response = await fetch(url.href, { headers: getAuthHeaders(), signal });
  if (!response.ok) throw new Error(`Could not load this message (HTTP ${response.status}).`);
  const data = await response.json();
  // Retained facades can ignore turn_id and return their full transcript. This
  // item read still selects exactly the requested identity, never a tail.
  const matches = Array.isArray(data.turns)
    ? data.turns.filter((turn: ConversationTurn) => turn.id === id)
    : [];
  const turn = data.turn ?? (matches.length === 1 ? matches[0] : undefined);
  if (turn?.id !== id || turn.history_preview)
    throw new Error('This Forge cannot expand this message. Update its history API.');
  return turn;
}
