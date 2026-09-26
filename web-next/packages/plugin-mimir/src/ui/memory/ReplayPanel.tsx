/** ReplayPanel — left panel shown in Replay mode: date, page counts, and a ticker of pages first seen that day. */
import { pagesKnownByDate, nodesFirstSeenOn } from '../../domain/replayHistogram';
import { kindLabel } from '../../domain/memoryKinds';
import type { MimirGraph } from '../../domain/api-types';

export interface ReplayPanelProps {
  graph: MimirGraph;
  asOf: string;
  onExitReplay: () => void;
  onFocus: (id: string) => void;
}

export function ReplayPanel({ graph, asOf, onExitReplay, onFocus }: ReplayPanelProps) {
  const knownByThen = pagesKnownByDate(graph.nodes, asOf);
  const bornThatDay = nodesFirstSeenOn(graph.nodes, asOf);
  const dateLabel = new Date(`${asOf}T00:00:00Z`).toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'long',
    timeZone: 'UTC',
  });

  return (
    <section
      className="niuu:w-80 niuu:max-h-full niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg niuu:p-4 niuu:flex niuu:flex-col niuu:gap-3 niuu:overflow-y-auto"
      aria-label="Replay"
    >
      <div className="niuu:flex niuu:items-center niuu:justify-between">
        <span className="niuu:text-xs niuu:text-brand-300 niuu:font-semibold niuu:uppercase niuu:tracking-widest">
          Replaying
        </span>
        <button
          type="button"
          onClick={onExitReplay}
          className="niuu:text-xs niuu:text-text-muted niuu:hover:text-text-primary"
        >
          Exit replay
        </button>
      </div>

      <h2 className="niuu:text-2xl niuu:font-semibold niuu:text-text-primary niuu:m-0">
        {dateLabel}
      </h2>

      <p className="niuu:text-sm niuu:text-text-secondary niuu:m-0">
        {knownByThen.toLocaleString()} pages known by then
        {bornThatDay.length > 0 ? ` · +${bornThatDay.length} that day` : ''}
      </p>

      {bornThatDay.length > 0 && (
        <ul className="niuu:flex niuu:flex-col niuu:gap-1.5 niuu:m-0 niuu:p-0 niuu:list-none">
          {bornThatDay.map((node) => (
            <li key={node.id}>
              <button
                type="button"
                onClick={() => onFocus(node.id)}
                className="niuu:w-full niuu:text-left niuu:px-2 niuu:py-1 niuu:rounded-sm niuu:hover:bg-bg-tertiary"
              >
                <span className="niuu:block niuu:text-sm niuu:text-text-secondary niuu:truncate">
                  {node.title}
                </span>
                <span className="niuu:block niuu:text-xs niuu:text-text-muted">
                  {kindLabel(node.kind)} ·{' '}
                  {new Date(node.firstSeen).toLocaleTimeString(undefined, {
                    hour: '2-digit',
                    minute: '2-digit',
                  })}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
