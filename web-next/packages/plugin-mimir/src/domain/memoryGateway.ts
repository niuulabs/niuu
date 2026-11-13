/**
 * What the memory gateway tells a person (its people routes): the hits a search finds across
 * every memory they read, and the changes waiting for them to keep or discard.
 */

/** One thing memory holds that matches a search, from one of the memories the person reads. */
export interface MemoryHit {
  /** `project:<id>` or `org`. */
  layer: string;
  /** The project's name, or "the org". */
  layerName: string;
  kind: 'page' | 'source';
  /** A page's path, or `source:<id>`. */
  ref: string;
  title: string;
  snippet: string;
}

export interface MemorySearchResult {
  query: string;
  hits: MemoryHit[];
}

/** One piece of a proposal's evidence. */
export interface ReviewSource {
  ref: string;
  kind: string;
  title: string | null;
  url: string | null;
  /** False for a source that was in a session's memory that has since been deleted. */
  kept: boolean;
}

/** A change waiting for a person to keep or discard it. */
export interface MemoryProposal {
  id: string;
  layer: string;
  layerName: string;
  title: string;
  /** What memory says now; null when the change replaces nothing. */
  now: string | null;
  would: string;
  why: string | null;
  by: 'agent' | 'person';
  createdAt: string;
  sources: ReviewSource[];
}

export interface MemoryReview {
  proposals: MemoryProposal[];
  /** Layers that had more waiting than the gateway read at once. */
  more: string[];
}

const TICKET_KEY = /\b([A-Z][A-Z0-9]+-\d+)\b/;

/** A short name for where a piece of evidence leads. */
export function sourceLabel(source: ReviewSource): string {
  if (source.title) return source.title;
  if (source.url) {
    const key = TICKET_KEY.exec(source.url);
    if (key?.[1]) return key[1];
    try {
      return new URL(source.url).hostname;
    } catch {
      return source.url;
    }
  }
  if (!source.kept) return 'a source no longer kept';
  switch (source.kind) {
    case 'session_event':
    case 'tool_call':
      return 'its work in a session';
    case 'commit':
      return 'a commit';
    case 'room_message':
      return 'a message in a room';
    case 'statement':
      return "a person's statement";
    case 'page':
      return 'a page';
    case 'fact':
      return 'a fact';
    case 'proposal':
      return 'an earlier proposal';
    default:
      return 'a source';
  }
}

/** A quiet line naming where a hit is held and what it is. */
export function hitPlace(hit: MemoryHit): string {
  return `${hit.layerName || 'memory'} · ${hit.kind}`;
}
