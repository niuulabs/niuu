/**
 * URL search-param shape for `/mimir` (the Memory Explore view), so Explore
 * / Focus / Replay all deep-link.
 *
 *   mount?  — scope the graph to one mount instance
 *   focus?  — node id — presence switches to the Focus inspector
 *   depth?  — 1 | 2 | 3 — Focus panel's Links depth control
 *   asOf?   — ISO date — presence switches to Replay mode
 *   view?   — '3d' | '2d' — scene render mode
 *   colour? — 'type' | 'proof' | 'age' — "Colour by" panel selection
 *
 * Asking a question navigates to the existing `/mimir/ask` route (its own
 * `q`/`mount` params) rather than living on `/mimir` itself.
 */
export interface MemoryViewSearch {
  mount?: string;
  focus?: string;
  depth?: 1 | 2 | 3;
  asOf?: string;
  view?: '3d' | '2d';
  colour?: 'type' | 'proof' | 'age';
}

const VALID_DEPTHS = new Set([1, 2, 3]);
const VALID_VIEWS = new Set(['3d', '2d']);
const VALID_COLOURS = new Set(['type', 'proof', 'age']);

function str(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined;
}

/**
 * TanStack Router `validateSearch` for `/mimir`. Unknown/invalid values are
 * dropped rather than throwing, so a hand-edited or stale URL degrades to
 * defaults instead of erroring the route.
 */
export function validateMemoryViewSearch(search: Record<string, unknown>): MemoryViewSearch {
  const rawDepth = Number(search.depth);
  const rawView = str(search.view);
  const rawColour = str(search.colour);

  return {
    mount: str(search.mount),
    focus: str(search.focus),
    depth: VALID_DEPTHS.has(rawDepth) ? (rawDepth as 1 | 2 | 3) : undefined,
    asOf: str(search.asOf),
    view: rawView && VALID_VIEWS.has(rawView) ? (rawView as '3d' | '2d') : undefined,
    colour:
      rawColour && VALID_COLOURS.has(rawColour)
        ? (rawColour as 'type' | 'proof' | 'age')
        : undefined,
  };
}
