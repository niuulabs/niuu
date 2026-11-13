/**
 * GraphSearch — the heads-up display's search box: type a name, pick a
 * match (Enter takes the best one), and the scene flies to it. In trace
 * mode the same box picks where a path from the focused node should go.
 *
 * A name that matches nothing is not a dead end: Enter asks memory the text
 * as a question instead.
 */
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { searchNodes } from '../../domain/hud';
import type { GraphNode } from '../../domain/api-types';
import './MemoryHud.css';

export interface GraphSearchProps {
  nodes: readonly GraphNode[];
  layerNames: ReadonlyMap<string, string>;
  /** `find` flies to a node; `trace` picks the end of a path from `traceFrom`. */
  mode: 'find' | 'trace';
  /** Title of the node a trace starts from (trace mode). */
  traceFrom?: string;
  /** One line under the box saying what just happened. */
  status: string;
  onSelect: (nodeId: string) => void;
  /** Enter on a name that matches nothing (find mode): ask memory the text instead. */
  onAsk: (question: string) => void;
  onCancelTrace: () => void;
}

export function GraphSearch({
  nodes,
  layerNames,
  mode,
  traceFrom,
  status,
  onSelect,
  onAsk,
  onCancelTrace,
}: GraphSearchProps) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listboxId = useId();
  const statusId = useId();

  // Trace mode is entered from the card; the box takes the keyboard at once.
  useEffect(() => {
    if (mode === 'trace') inputRef.current?.focus();
  }, [mode]);

  const matches = useMemo(() => searchNodes(nodes, query), [nodes, query]);
  const showList = open && matches.length > 0;
  const activeIndex = Math.min(active, Math.max(0, matches.length - 1));

  function choose(node: GraphNode) {
    setQuery(mode === 'find' ? node.title : '');
    setOpen(false);
    setActive(0);
    onSelect(node.id);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'ArrowDown' && matches.length > 0) {
      event.preventDefault();
      setOpen(true);
      setActive((i) => Math.min(i + 1, matches.length - 1));
      return;
    }
    if (event.key === 'ArrowUp' && matches.length > 0) {
      event.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      const match = matches[activeIndex];
      if (match) {
        choose(match);
        return;
      }
      const text = query.trim();
      if (mode === 'find' && text.length > 0) {
        setOpen(false);
        onAsk(text);
      }
      return;
    }
    if (event.key === 'Escape') {
      if (showList) {
        // Close the list only; Escape's step-back belongs to the next press.
        event.stopPropagation();
        setOpen(false);
        return;
      }
      if (mode === 'trace') {
        event.stopPropagation();
        onCancelTrace();
      }
    }
  }

  const label = mode === 'trace' ? 'Trace a path to' : 'Search the knowledge graph';
  const placeholder =
    mode === 'trace'
      ? `Trace a path from ${traceFrom ?? 'here'} to…`
      : 'Type a name: a person, a decision, a ticket…';

  return (
    <div className="niuu:relative niuu:flex niuu:flex-col niuu:gap-2">
      <p className="niuu-hud-caption">{mode === 'trace' ? 'Memory · trace' : 'Memory'}</p>
      <input
        type="text"
        role="combobox"
        aria-label={label}
        aria-expanded={showList}
        aria-controls={listboxId}
        aria-autocomplete="list"
        aria-describedby={statusId}
        aria-activedescendant={showList ? `${listboxId}-${activeIndex}` : undefined}
        ref={inputRef}
        value={query}
        placeholder={placeholder}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
          setActive(0);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
        className="niuu-hud-search-input"
      />
      {showList && (
        <ul
          id={listboxId}
          role="listbox"
          aria-label={`${matches.length} matching ${matches.length === 1 ? 'name' : 'names'}`}
          className="niuu-hud-glass niuu-hud-listbox niuu:absolute niuu:left-0 niuu:right-0 niuu:top-full niuu:mt-1 niuu:z-10"
        >
          {matches.map((node, i) => (
            <li
              key={node.id}
              id={`${listboxId}-${i}`}
              role="option"
              aria-selected={i === activeIndex}
              // mousedown, not click: the input's blur would close the list first.
              onMouseDown={(e) => {
                e.preventDefault();
                choose(node);
              }}
              onMouseEnter={() => setActive(i)}
              className="niuu-hud-option"
            >
              <span className="niuu:truncate">{node.title}</span>
              <span className="niuu-hud-mono niuu:flex-shrink-0">
                {(node.kind ?? 'page').toUpperCase()} ·{' '}
                {(layerNames.get(node.mount) ?? node.mount).toUpperCase()}
              </span>
            </li>
          ))}
        </ul>
      )}
      <p id={statusId} className="niuu:m-0 niuu:text-xs niuu:text-text-muted" role="status">
        {status}
      </p>
    </div>
  );
}
