/**
 * ColourByPanel — "Colour by" panel, visible in every Memory Explore mode.
 * Type: per-kind-group checkboxes (only groups present in the graph) +
 * "Unanswered questions". Proof: confidence-tier counts. Age: recency
 * bucket counts.
 */
import { SegmentedFilter } from '@niuulabs/ui';
import { KIND_GROUPS } from '../../domain/memoryKinds';
import type { KindGroup } from '../../domain/memoryKinds';
import { AGE_BUCKETS } from '../../domain/ageBucket';
import {
  countsByKindGroup,
  countsByConfidence,
  countsByAgeBucket,
} from '../../domain/legendCounts';
import type { MimirGraph } from '../../domain/api-types';
import type { ColourBy } from '../scene/types';
import './MemoryLegend.css';

const CONFIDENCE_LABEL: Record<string, string> = { high: 'High', medium: 'Medium', low: 'Low' };

interface ColourByPanelProps {
  graph: MimirGraph;
  colour: ColourBy;
  onColourChange: (colour: ColourBy) => void;
  hiddenGroups: ReadonlySet<KindGroup>;
  onToggleGroup: (id: KindGroup) => void;
  showQuestions: boolean;
  onToggleQuestions: () => void;
  questionCount: number;
}

export function ColourByPanel({
  graph,
  colour,
  onColourChange,
  hiddenGroups,
  onToggleGroup,
  showQuestions,
  onToggleQuestions,
  questionCount,
}: ColourByPanelProps) {
  const kindCounts = countsByKindGroup(graph.nodes);

  return (
    <section
      className="niuu:w-72 niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg niuu:p-4 niuu:flex niuu:flex-col niuu:gap-3"
      aria-label="Colour by"
    >
      <div className="niuu:flex niuu:items-center niuu:justify-between">
        <span className="niuu:text-xs niuu:text-text-muted niuu:uppercase niuu:tracking-widest">
          Colour by
        </span>
        <SegmentedFilter
          aria-label="Colour by"
          value={colour}
          onChange={onColourChange}
          options={[
            { value: 'type', label: 'Type' },
            { value: 'proof', label: 'Proof' },
            { value: 'age', label: 'Age' },
          ]}
        />
      </div>

      {colour === 'type' && (
        <div className="niuu:flex niuu:flex-col niuu:gap-1.5">
          {kindCounts.map(({ id, count }) => {
            const group = KIND_GROUPS.find((g) => g.id === id)!;
            const hidden = hiddenGroups.has(id);
            return (
              <label
                key={id}
                className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-sm niuu:text-text-secondary niuu:cursor-pointer"
              >
                <input
                  type="checkbox"
                  checked={!hidden}
                  onChange={() => onToggleGroup(id)}
                  aria-label={group.label}
                />
                <span className="memory-swatch" data-group={id} aria-hidden />
                <span className="niuu:flex-1">{group.label}</span>
                <span className="niuu:font-mono niuu:text-xs niuu:text-text-muted">{count}</span>
              </label>
            );
          })}
          {kindCounts.length === 0 && (
            <p className="niuu:text-xs niuu:text-text-muted niuu:italic niuu:m-0">No pages yet</p>
          )}
          <label className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-sm niuu:text-text-secondary niuu:cursor-pointer niuu:pt-2 niuu:mt-1 niuu:border-t niuu:border-border-subtle">
            <input
              type="checkbox"
              checked={showQuestions}
              onChange={onToggleQuestions}
              aria-label="Unanswered questions"
            />
            <span className="niuu:flex-1">Unanswered questions</span>
            <span className="niuu:font-mono niuu:text-xs niuu:text-text-muted">
              {questionCount}
            </span>
          </label>
        </div>
      )}

      {colour === 'proof' && (
        <div className="niuu:flex niuu:flex-col niuu:gap-1.5">
          {countsByConfidence(graph.nodes).map(({ id, count }) => (
            <div
              key={id}
              className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-sm niuu:text-text-secondary"
            >
              <span className="niuu:flex-1">{CONFIDENCE_LABEL[id]}</span>
              <span className="niuu:font-mono niuu:text-xs niuu:text-text-muted">{count}</span>
            </div>
          ))}
        </div>
      )}

      {colour === 'age' && (
        <div className="niuu:flex niuu:flex-col niuu:gap-1.5">
          {countsByAgeBucket(graph.nodes).map(({ id, count }) => {
            const bucket = AGE_BUCKETS.find((b) => b.id === id)!;
            return (
              <div
                key={id}
                className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-sm niuu:text-text-secondary"
              >
                <span className="niuu:flex-1">{bucket.label}</span>
                <span className="niuu:font-mono niuu:text-xs niuu:text-text-muted">{count}</span>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
