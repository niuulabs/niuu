/**
 * HudCard — the glass heads-up card for the focused item: its kind and
 * layer as a mono caption, its name, the one-sentence summary from the
 * knowledge graph, what changed this week, and the two ways onward.
 */
import type { GraphNode } from '../../domain/api-types';
import './MemoryHud.css';

export interface HudCardProps {
  node: GraphNode;
  layerName: string;
  /** "This week: …" for a recent change, or null. */
  changeLine: string | null;
  /** True while the search box is picking the end of a path from this node. */
  tracing: boolean;
  onOpenPage: () => void;
  onTracePath: () => void;
}

export function HudCard({
  node,
  layerName,
  changeLine,
  tracing,
  onOpenPage,
  onTracePath,
}: HudCardProps) {
  return (
    <aside
      aria-label="Focused"
      className="niuu-hud-glass niuu:w-72 niuu:p-4 niuu:flex niuu:flex-col niuu:gap-3"
    >
      <p className="niuu-hud-caption">
        {node.kind ?? 'page'} · {layerName}
      </p>
      <hr className="niuu-hud-rule" />
      <h2 className="niuu:m-0 niuu:text-lg niuu:font-semibold niuu:text-text-primary">
        {node.title}
      </h2>
      {node.summary ? (
        <p className="niuu:m-0 niuu:text-sm niuu:text-text-secondary">{node.summary}</p>
      ) : (
        <p className="niuu:m-0 niuu:text-sm niuu:text-text-muted niuu:italic">
          No summary written yet.
        </p>
      )}
      {changeLine && <p className="niuu:m-0 niuu:text-sm niuu-hud-recent">{changeLine}</p>}
      <button
        type="button"
        onClick={onOpenPage}
        className="niuu-hud-action niuu-hud-action--primary"
      >
        Open the page
      </button>
      <button
        type="button"
        aria-pressed={tracing}
        onClick={onTracePath}
        className="niuu-hud-action niuu-hud-action--secondary"
      >
        Trace a path to…
      </button>
    </aside>
  );
}
