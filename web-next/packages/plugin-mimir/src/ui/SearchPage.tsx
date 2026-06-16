import { useNavigate } from '@tanstack/react-router';
import { usePluginCtx } from '@niuulabs/plugin-sdk';
import { StateDot } from '@niuulabs/ui';
import { useSearch } from '../application/useSearch';
import { useActiveMount } from '../application/useActiveMount';
import type { SearchMode } from '../ports';
import './mimir-views.css';

const MODES: SearchMode[] = ['fts', 'semantic', 'hybrid'];

const MODE_LABELS: Record<SearchMode, string> = {
  fts: 'FTS',
  semantic: 'SEMANTIC',
  hybrid: 'HYBRID',
};

// ---------------------------------------------------------------------------
// Highlight utility
// ---------------------------------------------------------------------------

function highlightText(text: string, query: string): React.ReactNode {
  const trimmed = query.trim();
  if (!trimmed) return text;

  const words = trimmed.split(/\s+/).filter((w) => w.length > 1);
  if (words.length === 0) return text;

  const escaped = words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const pattern = new RegExp(`(${escaped.join('|')})`, 'gi');
  const parts = text.split(pattern);
  if (parts.length <= 1) return text;

  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <mark key={i} className="mm-highlight">
            {part}
          </mark>
        ) : (
          part || null
        ),
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// SearchPage — matches web2 mimir/design/views.jsx SearchView layout
// ---------------------------------------------------------------------------

export interface SearchPageProps {
  /** Start with the retrieval-workbench debug toggle enabled. */
  initialDebug?: boolean;
}

export function SearchPage({ initialDebug = false }: SearchPageProps = {}) {
  const navigate = useNavigate();
  const ctx = usePluginCtx();
  const { activeMount, mountName } = useActiveMount();
  const { query, mode, debug, setQuery, setMode, setDebug, results, isLoading, isError, error } =
    useSearch(mountName, initialDebug);
  const mountLabel = activeMount === 'all' ? 'all mounts' : activeMount;
  const placeholder =
    activeMount === 'all' ? 'Search pages across all mounts…' : `Search pages in ${activeMount}…`;

  function openResult(path: string, mounts?: string[]) {
    ctx.setTweak('mimir.selectedPagePath', path);
    if (activeMount === 'all' && mounts?.length === 1) {
      ctx.setTweak('activeMount', mounts[0]);
    }
    navigate({ to: '/mimir/pages' });
  }

  return (
    <div className="niuu:flex niuu:flex-col niuu:h-full">
      {/* Search header bar */}
      <div className="niuu:flex niuu:items-center niuu:gap-3 niuu:px-5 niuu:py-4 niuu:border-b niuu:border-border-subtle">
        <input
          className="niuu:flex-1 niuu:px-3 niuu:py-2.5 niuu:bg-bg-secondary niuu:rounded-sm niuu:text-text-primary niuu:font-mono niuu:text-[13px] niuu:outline-none"
          style={{ border: 'none' }}
          type="search"
          placeholder={placeholder}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search query"
        />

        <div
          className="niuu:flex niuu:items-center niuu:gap-[2px] niuu:bg-bg-tertiary niuu:p-[2px] niuu:rounded-sm niuu:border niuu:border-border-subtle"
          role="group"
          aria-label="Search mode"
        >
          {MODES.map((m) => (
            <button
              key={m}
              className={[
                'niuu:px-2.5 niuu:py-1 niuu:rounded-[calc(var(--radius-sm)-2px)] niuu:font-mono niuu:text-[10px] niuu:uppercase niuu:tracking-wider niuu:cursor-pointer niuu:border-none niuu:transition-colors',
                m === mode
                  ? 'niuu:bg-bg-elevated niuu:text-brand-300'
                  : 'niuu:bg-transparent niuu:text-text-muted niuu:hover:text-text-secondary',
              ].join(' ')}
              onClick={() => setMode(m)}
              aria-pressed={m === mode}
              data-mode={m}
            >
              {MODE_LABELS[m]}
            </button>
          ))}
        </div>

        <button
          type="button"
          className={[
            'niuu:px-2.5 niuu:py-1 niuu:rounded-sm niuu:font-mono niuu:text-[10px] niuu:uppercase niuu:tracking-wider niuu:cursor-pointer niuu:border niuu:border-solid niuu:transition-colors',
            debug
              ? 'niuu:bg-bg-elevated niuu:text-brand-300 niuu:border-border'
              : 'niuu:bg-bg-tertiary niuu:text-text-muted niuu:border-border-subtle niuu:hover:text-text-secondary',
          ].join(' ')}
          onClick={() => setDebug(!debug)}
          aria-pressed={debug}
          data-testid="debug-toggle"
        >
          debug
        </button>
        <div className="niuu:flex niuu:flex-col niuu:items-end niuu:gap-0.5">
          <span className="niuu:font-mono niuu:text-[10px] niuu:text-text-muted">
            {results.length} results
          </span>
          <span className="niuu:font-mono niuu:text-[10px] niuu:text-text-faint">{mountLabel}</span>
        </div>
      </div>

      {isLoading && (
        <div className="niuu:flex niuu:items-center niuu:gap-2 niuu:p-5">
          <StateDot state="processing" pulse />
          <span className="niuu:text-sm niuu:text-text-secondary">searching…</span>
        </div>
      )}

      {isError && (
        <div className="niuu:flex niuu:items-center niuu:gap-2 niuu:p-5">
          <StateDot state="failed" />
          <span className="niuu:text-sm niuu:text-text-secondary">
            {error instanceof Error ? error.message : 'search failed'}
          </span>
        </div>
      )}

      {!isLoading && query.trim().length > 0 && results.length === 0 && !isError && (
        <p className="niuu:text-sm niuu:text-text-muted niuu:p-5">
          No results found for &ldquo;{query}&rdquo;
        </p>
      )}

      {/* Results list */}
      {results.length > 0 && (
        <div
          className="niuu:flex niuu:flex-col niuu:overflow-y-auto niuu:flex-1"
          aria-label="Search results"
        >
          {results.map((result) => (
            <button
              type="button"
              key={result.path}
              className="niuu:w-full niuu:py-3 niuu:px-5 niuu:border-b niuu:border-border niuu:cursor-pointer niuu:hover:bg-bg-tertiary niuu:text-left niuu:bg-transparent niuu:border-x-0 niuu:border-t-0"
              data-testid="search-result"
              onClick={() => openResult(result.path, result.mounts)}
            >
              {/* Title + score */}
              <div className="niuu:flex niuu:items-baseline niuu:gap-3">
                <span className="niuu:font-medium niuu:text-sm niuu:text-text-primary niuu:flex-1">
                  {highlightText(result.title, query)}
                </span>
                {typeof result.score === 'number' && (
                  <span className="niuu:font-mono niuu:text-[10px] niuu:text-text-faint niuu:shrink-0">
                    score {result.score.toFixed(2)}
                  </span>
                )}
              </div>

              {/* Path on its own line */}
              <div className="niuu:font-mono niuu:text-[10px] niuu:text-text-muted niuu:mt-[2px]">
                {result.path}
              </div>

              {/* Summary */}
              <p className="niuu:text-xs niuu:leading-normal niuu:text-text-secondary niuu:m-0 niuu:mt-1">
                {highlightText(result.summary, query)}
              </p>

              {/* Chips row */}
              <div className="niuu:flex niuu:items-center niuu:gap-1 niuu:mt-1.5">
                <span className="mm-chip accent">
                  <span className="mm-chip-k">type</span>{' '}
                  {result.type?.toUpperCase() ?? result.category?.toUpperCase()}
                </span>
                <span
                  className={`mm-chip ${result.confidence === 'high' ? 'ok' : result.confidence === 'medium' ? 'warn' : 'err'}`}
                >
                  <span className="mm-chip-k">conf</span>{' '}
                  <strong>{result.confidence?.toUpperCase()}</strong>
                </span>
                {result.mounts?.map((mount) => (
                  <span key={mount} className="mm-chip">
                    {mount}
                  </span>
                ))}
              </div>

              {/* Debug: per-factor score breakdown */}
              {debug && result.scoreBreakdown && (
                <div
                  className="niuu:flex niuu:items-center niuu:flex-wrap niuu:gap-1 niuu:mt-1.5"
                  data-testid="score-breakdown"
                  aria-label={`Score breakdown for ${result.path}`}
                >
                  {Object.entries(result.scoreBreakdown).map(([factor, value]) => (
                    <span key={factor} className={`mm-chip${factor === 'final' ? ' accent' : ''}`}>
                      <span className="mm-chip-k">{factor}</span>{' '}
                      {typeof value === 'number' ? value.toFixed(2) : '—'}
                    </span>
                  ))}
                </div>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
