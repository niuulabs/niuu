/**
 * ExplorePanel — left "What Niuu knows" panel shown in the default Explore
 * mode: stats, find-a-page, fly-to mounts, most-connected pages, the "Right
 * now" live feed, and the "Add a source" ingest form.
 */
import { useMemo, useRef, useState } from 'react';
import { pagesPerMount } from '../../domain/graphIndex';
import { LoadingState, StateDot } from '@niuulabs/ui';
import { topConnected } from '../../domain/graphDegree';
import { describeLiveActivity } from '../../domain/liveActivityText';
import { useIngestSource } from '../../application/useIngestSource';
import type { MimirGraph, LiveActivity } from '../../domain/api-types';

const MOST_CONNECTED_LIMIT = 5;
const RIGHT_NOW_LIMIT = 3;

export interface ExplorePanelProps {
  graph: MimirGraph;
  liveActivity: LiveActivity[] | undefined;
  liveActivityIsError: boolean;
  onFocus: (id: string) => void;
  onFlyToMount: (mountName: string) => void;
}

function counted(n: number, one: string, many: string): string {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}

export function ExplorePanel({
  graph,
  liveActivity,
  liveActivityIsError,
  onFocus,
  onFlyToMount,
}: ExplorePanelProps) {
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    return graph.nodes.filter((n) => n.title.toLowerCase().includes(q)).slice(0, 8);
  }, [graph.nodes, query]);

  const mostConnected = useMemo(() => topConnected(graph, MOST_CONNECTED_LIMIT), [graph]);
  const mounts = useMemo(() => pagesPerMount(graph), [graph]);
  const statsLine = [
    counted(graph.nodes.length, 'page', 'pages'),
    counted(graph.edges.length, 'link', 'links'),
    counted(mounts.length, 'instance', 'instances'),
  ].join(' · ');

  const titleForPath = useMemo(() => {
    const byPath = new Map<string, string>();
    for (const node of graph.nodes) byPath.set(node.path ?? node.id, node.title);
    return byPath;
  }, [graph.nodes]);

  function handleFindKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (matches.length === 0) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, matches.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const target = matches[activeIndex] ?? matches[0];
      if (target) onFocus(target.id);
    }
  }

  return (
    <section
      className="niuu:w-80 niuu:max-h-full niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg niuu:p-4 niuu:flex niuu:flex-col niuu:gap-4 niuu:overflow-y-auto"
      aria-label="What Niuu knows"
    >
      <div>
        <h2 className="niuu:text-lg niuu:font-semibold niuu:text-text-primary niuu:m-0">
          What Niuu knows
        </h2>
        <p className="niuu:text-xs niuu:text-text-muted niuu:m-0 niuu:mt-1">{statsLine}</p>
      </div>

      {/* ── Find a page ─────────────────────────────────────────── */}
      <div>
        <input
          type="text"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActiveIndex(0);
          }}
          onKeyDown={handleFindKeyDown}
          placeholder="Find a page…"
          aria-label="Find a page"
          role="combobox"
          aria-expanded={matches.length > 0}
          aria-controls="memory-find-listbox"
          className="niuu:w-full niuu:bg-bg-primary niuu:border niuu:border-border niuu:rounded-sm niuu:px-3 niuu:py-1.5 niuu:text-sm niuu:text-text-primary niuu:placeholder:text-text-muted niuu:focus:outline-none niuu:focus:border-brand-300"
        />
        {matches.length > 0 && (
          <ul
            id="memory-find-listbox"
            role="listbox"
            aria-label="Matching pages"
            className="niuu:mt-1 niuu:flex niuu:flex-col niuu:gap-0.5"
          >
            {matches.map((node, i) => (
              <li key={node.id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={i === activeIndex}
                  onClick={() => onFocus(node.id)}
                  onMouseEnter={() => setActiveIndex(i)}
                  className={[
                    'niuu:w-full niuu:text-left niuu:px-2 niuu:py-1 niuu:rounded-sm niuu:text-sm',
                    i === activeIndex
                      ? 'niuu:bg-bg-tertiary niuu:text-text-primary'
                      : 'niuu:text-text-secondary niuu:hover:bg-bg-tertiary',
                  ].join(' ')}
                >
                  {node.title}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* ── Fly to ───────────────────────────────────────────────── */}
      {mounts.length > 0 && (
        <div>
          <h3 className="niuu:text-xs niuu:text-text-muted niuu:uppercase niuu:tracking-widest niuu:m-0 niuu:mb-1.5">
            Fly to
          </h3>
          <ul className="niuu:flex niuu:flex-col niuu:gap-0.5 niuu:m-0 niuu:p-0 niuu:list-none">
            {mounts.map((m) => (
              <li key={m.mount}>
                <button
                  type="button"
                  onClick={() => onFlyToMount(m.mount)}
                  className="niuu:w-full niuu:flex niuu:items-center niuu:justify-between niuu:px-2 niuu:py-1 niuu:rounded-sm niuu:text-sm niuu:text-text-secondary niuu:hover:bg-bg-tertiary niuu:hover:text-text-primary"
                >
                  <span>{m.mount}</span>
                  <span className="niuu:font-mono niuu:text-xs niuu:text-text-muted">
                    {m.pages.toLocaleString()}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* ── Most connected ──────────────────────────────────────── */}
      {mostConnected.length > 0 && (
        <div>
          <h3 className="niuu:text-xs niuu:text-text-muted niuu:uppercase niuu:tracking-widest niuu:m-0 niuu:mb-1.5">
            Most connected
          </h3>
          <ul className="niuu:flex niuu:flex-col niuu:gap-0.5 niuu:m-0 niuu:p-0 niuu:list-none">
            {mostConnected.map(({ node, degree }) => (
              <li key={node.id}>
                <button
                  type="button"
                  onClick={() => onFocus(node.id)}
                  className="niuu:w-full niuu:flex niuu:items-center niuu:justify-between niuu:px-2 niuu:py-1 niuu:rounded-sm niuu:text-sm niuu:text-text-secondary niuu:hover:bg-bg-tertiary niuu:hover:text-text-primary"
                >
                  <span className="niuu:truncate">{node.title}</span>
                  <span className="niuu:font-mono niuu:text-xs niuu:text-text-muted niuu:flex-shrink-0 niuu:ml-2">
                    {degree}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* ── Right now ────────────────────────────────────────────── */}
      <div>
        <h3 className="niuu:text-xs niuu:text-text-muted niuu:uppercase niuu:tracking-widest niuu:m-0 niuu:mb-1.5">
          Right now
        </h3>
        {liveActivityIsError && (
          <p className="niuu:text-xs niuu:text-critical niuu:m-0" role="alert">
            Live activity is unavailable right now.
          </p>
        )}
        {!liveActivityIsError && liveActivity === undefined && (
          <LoadingState label="loading activity…" />
        )}
        {!liveActivityIsError && liveActivity !== undefined && liveActivity.length === 0 && (
          <p className="niuu:text-xs niuu:text-text-muted niuu:italic niuu:m-0">
            Nothing happening right now.
          </p>
        )}
        {!liveActivityIsError && liveActivity !== undefined && liveActivity.length > 0 && (
          <ul className="niuu:flex niuu:flex-col niuu:gap-1 niuu:m-0 niuu:p-0 niuu:list-none">
            {liveActivity.slice(0, RIGHT_NOW_LIMIT).map((entry) => (
              <li
                key={entry.id}
                className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-sm niuu:text-text-secondary"
              >
                <StateDot state="processing" pulse />
                <span className="niuu:truncate">
                  {describeLiveActivity(entry, titleForPath.get(entry.path) ?? entry.path)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* ── Add a source ─────────────────────────────────────────── */}
      <AddSourceForm />
    </section>
  );
}

export function AddSourceForm() {
  const [url, setUrl] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { ingest, isPending, isError, error, data, reset } = useIngestSource(() => {
    setUrl('');
    if (fileInputRef.current) fileInputRef.current.value = '';
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim()) return;
    reset();
    ingest({ type: 'url', url: url.trim() });
  }

  function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    reset();
    ingest({ type: 'file', file });
  }

  return (
    <div className="niuu:pt-2 niuu:border-t niuu:border-border-subtle">
      <h3 className="niuu:text-xs niuu:text-text-muted niuu:uppercase niuu:tracking-widest niuu:m-0 niuu:mb-1.5">
        Add a source
      </h3>
      <form
        onSubmit={handleSubmit}
        className="niuu:flex niuu:gap-2 niuu:mb-2"
        aria-label="Ingest a URL"
      >
        <input
          type="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://…"
          disabled={isPending}
          aria-label="Source URL"
          className="niuu:flex-1 niuu:bg-bg-primary niuu:border niuu:border-border niuu:rounded-sm niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-primary niuu:font-mono niuu:placeholder:text-text-muted"
        />
        <button
          type="submit"
          disabled={isPending || !url.trim()}
          className="niuu:px-3 niuu:py-1 niuu:text-xs niuu:rounded-sm niuu:bg-brand niuu:text-bg-primary niuu:disabled:opacity-50"
        >
          Add
        </button>
      </form>
      <label className="niuu:block niuu:text-xs niuu:text-brand-300 niuu:cursor-pointer">
        or choose a file
        <input
          ref={fileInputRef}
          type="file"
          className="niuu:sr-only"
          onChange={handleFile}
          disabled={isPending}
          aria-label="Upload a source file"
        />
      </label>
      {isPending && (
        <p className="niuu:text-xs niuu:text-text-secondary niuu:m-0 niuu:mt-1">Adding source…</p>
      )}
      {isError && (
        <p className="niuu:text-xs niuu:text-critical niuu:m-0 niuu:mt-1" role="alert">
          {error instanceof Error ? error.message : 'ingest failed'}
        </p>
      )}
      {!isError && data && (
        <p className="niuu:text-xs niuu:text-brand-200 niuu:m-0 niuu:mt-1" role="status">
          Added &quot;{data.title}&quot;.
        </p>
      )}
    </div>
  );
}
