/**
 * AnalyticsPage — retrieval quality and query-traffic analytics.
 *
 * Top: metric tiles (P@5 / MRR / R@10 overall + query volume).
 * Middle: per-category eval table (dense, monospace, left-aligned).
 * Bottom: zero-result queries + recent query log.
 *
 * Both data sources are optional backend capabilities — each section
 * renders a graceful empty state when the backend returns null.
 */

import { StateDot } from '@niuulabs/ui';
import { useEvalReport, useQueryStats } from '../application/useAnalytics';
import type { EvalMetrics, EvalReport, QueryStats } from '../domain/analytics';
import { zeroResultQueries } from '../domain/analytics';
import { formatTimestamp } from './format';

const STATUS_ROW = 'niuu:flex niuu:items-center niuu:gap-2 niuu:text-sm niuu:text-text-secondary';

const KPI_LBL = 'niuu:text-[10px] niuu:uppercase niuu:tracking-[0.07em] niuu:text-text-muted';
const KPI_VAL = 'niuu:text-2xl niuu:font-medium niuu:font-mono niuu:tracking-[-0.01em]';
const KPI_SUB =
  'niuu:text-[10px] niuu:text-text-muted niuu:font-mono niuu:whitespace-nowrap niuu:overflow-hidden niuu:text-ellipsis';
const KPI_CELL =
  'niuu:flex niuu:flex-col niuu:gap-[2px] niuu:py-3 niuu:px-4 niuu:bg-bg-secondary ' +
  'niuu:border-r niuu:border-solid niuu:border-0 niuu:border-r-border-subtle niuu:last:border-r-0';

const TABLE_HEAD =
  'niuu:text-[9px] niuu:uppercase niuu:tracking-[0.07em] niuu:text-text-muted niuu:font-medium niuu:text-left niuu:py-[3px]';
const TABLE_CELL = 'niuu:font-mono niuu:text-[11px] niuu:text-text-secondary niuu:py-[3px]';

function formatMetric(value: number): string {
  return value.toFixed(2);
}

// ── Metric tiles ─────────────────────────────────────────────────────────────

interface MetricTilesProps {
  report: EvalReport;
}

function MetricTiles({ report }: MetricTilesProps) {
  const generated = formatTimestamp(report.generatedAt, 'medium');
  const tiles: Array<{ label: string; value: string; sub: string }> = [
    { label: 'precision @5', value: formatMetric(report.overall.precisionAt5), sub: generated },
    { label: 'mrr', value: formatMetric(report.overall.mrr), sub: generated },
    { label: 'recall @10', value: formatMetric(report.overall.recallAt10), sub: generated },
    { label: 'eval queries', value: String(report.queryCount), sub: 'benchmark set' },
  ];
  return (
    <div
      className="niuu:grid niuu:grid-cols-4 niuu:border niuu:border-solid niuu:border-border-subtle niuu:rounded-md niuu:overflow-hidden"
      data-testid="eval-tiles"
    >
      {tiles.map((tile) => (
        <div key={tile.label} className={KPI_CELL} data-testid="eval-tile">
          <span className={KPI_LBL}>{tile.label}</span>
          <span className={`${KPI_VAL} niuu:text-brand-300`}>{tile.value}</span>
          <span className={KPI_SUB}>{tile.sub}</span>
        </div>
      ))}
    </div>
  );
}

// ── Per-category table ───────────────────────────────────────────────────────

interface CategoryTableProps {
  byCategory: Record<string, EvalMetrics>;
}

function CategoryTable({ byCategory }: CategoryTableProps) {
  const categories = Object.keys(byCategory).sort();
  if (categories.length === 0) {
    return <p className="niuu:text-sm niuu:text-text-muted niuu:m-0">No per-category metrics.</p>;
  }
  return (
    <table className="niuu:w-full niuu:border-collapse niuu:text-left" data-testid="category-table">
      <thead>
        <tr className="niuu:border-0 niuu:border-b niuu:border-solid niuu:border-border-subtle">
          <th className={TABLE_HEAD}>category</th>
          <th className={TABLE_HEAD}>p@5</th>
          <th className={TABLE_HEAD}>mrr</th>
          <th className={TABLE_HEAD}>r@10</th>
        </tr>
      </thead>
      <tbody>
        {categories.map((category) => {
          const metrics = byCategory[category]!;
          return (
            <tr
              key={category}
              className="niuu:border-0 niuu:border-b niuu:border-solid niuu:border-[color-mix(in_srgb,var(--color-border-subtle)_40%,transparent)] niuu:hover:bg-bg-tertiary"
              data-testid="category-row"
            >
              <td className={`${TABLE_CELL} niuu:text-text-primary`}>{category}</td>
              <td className={TABLE_CELL}>{formatMetric(metrics.precisionAt5)}</td>
              <td className={TABLE_CELL}>{formatMetric(metrics.mrr)}</td>
              <td className={TABLE_CELL}>{formatMetric(metrics.recallAt10)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// ── Query traffic ────────────────────────────────────────────────────────────

interface QueryTrafficProps {
  stats: QueryStats;
}

function QueryTraffic({ stats }: QueryTrafficProps) {
  const misses = zeroResultQueries(stats);
  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-6" data-testid="query-traffic">
      <div className="niuu:flex niuu:items-baseline niuu:gap-3">
        <h3 className="niuu:text-base niuu:font-semibold niuu:m-0">Query traffic</h3>
        <span className="niuu:text-xs niuu:text-text-muted niuu:font-mono">
          {stats.total} total · {stats.zeroResultCount} zero-result
        </span>
      </div>

      {/* Zero-result queries */}
      <section aria-label="Zero-result queries">
        <h4 className="niuu:text-xs niuu:uppercase niuu:tracking-[0.07em] niuu:text-text-muted niuu:m-0 niuu:mb-2">
          Zero-result queries
        </h4>
        {misses.length === 0 && (
          <p className="niuu:text-sm niuu:text-text-muted niuu:m-0" data-testid="zero-empty">
            No zero-result queries in the recent log.
          </p>
        )}
        {misses.length > 0 && (
          <ul className="niuu:list-none niuu:p-0 niuu:m-0 niuu:flex niuu:flex-col niuu:gap-1">
            {misses.map((entry) => (
              <li
                key={`${entry.ts}-${entry.query}`}
                className="niuu:flex niuu:items-center niuu:gap-3 niuu:font-mono niuu:text-[11px]"
                data-testid="zero-result-query"
              >
                <span className="niuu:text-text-faint">{formatTimestamp(entry.ts, 'short')}</span>
                <span className="niuu:text-critical-fg">0</span>
                <span className="niuu:text-text-secondary">{entry.query}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Recent query log */}
      <section aria-label="Recent queries">
        <h4 className="niuu:text-xs niuu:uppercase niuu:tracking-[0.07em] niuu:text-text-muted niuu:m-0 niuu:mb-2">
          Recent queries
        </h4>
        {stats.recent.length === 0 && (
          <p className="niuu:text-sm niuu:text-text-muted niuu:m-0" data-testid="recent-empty">
            No queries logged yet.
          </p>
        )}
        {stats.recent.length > 0 && (
          <div
            className="niuu:font-mono niuu:text-[11px] niuu:text-text-secondary niuu:leading-[1.7]"
            data-testid="query-log"
          >
            <div className="niuu:grid niuu:grid-cols-[110px_50px_1fr] niuu:gap-3 niuu:py-[2px] niuu:text-text-muted niuu:uppercase niuu:tracking-[0.07em] niuu:text-[9px] niuu:font-medium">
              <span>time</span>
              <span>hits</span>
              <span>query</span>
            </div>
            {stats.recent.map((entry) => (
              <div
                key={`${entry.ts}-${entry.query}`}
                className="niuu:grid niuu:grid-cols-[110px_50px_1fr] niuu:gap-3 niuu:py-[3px] niuu:border-0 niuu:border-b niuu:border-solid niuu:border-[color-mix(in_srgb,var(--color-border-subtle)_40%,transparent)] niuu:items-center niuu:hover:bg-bg-tertiary"
                data-testid="query-row"
              >
                <span className="niuu:text-text-faint niuu:truncate">
                  {formatTimestamp(entry.ts, 'short')}
                </span>
                <span
                  className={
                    entry.resultCount === 0 ? 'niuu:text-critical-fg' : 'niuu:text-brand-300'
                  }
                >
                  {entry.resultCount}
                </span>
                <span className="niuu:text-text-primary niuu:truncate">{entry.query}</span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export function AnalyticsPage() {
  const evalReport = useEvalReport();
  const queryStats = useQueryStats();

  return (
    <div className="niuu:p-6 niuu:max-w-[960px] niuu:flex niuu:flex-col niuu:gap-6">
      <div>
        <h2 className="niuu:text-xl niuu:font-semibold niuu:m-0 niuu:mb-2">Analytics</h2>
        <p className="niuu:text-sm niuu:text-text-secondary niuu:m-0">
          Retrieval quality benchmarks and live query traffic for this Mímir deployment.
        </p>
      </div>

      {/* ── Eval report ────────────────────────────────────────── */}
      {evalReport.isLoading && (
        <div className={STATUS_ROW}>
          <StateDot state="processing" pulse />
          <span>loading eval report…</span>
        </div>
      )}

      {evalReport.isError && (
        <div className={STATUS_ROW}>
          <StateDot state="failed" />
          <span>
            {evalReport.error instanceof Error
              ? evalReport.error.message
              : 'eval report load failed'}
          </span>
        </div>
      )}

      {!evalReport.isLoading && !evalReport.isError && evalReport.data === null && (
        <p className="niuu:text-sm niuu:text-text-muted niuu:m-0" data-testid="eval-empty">
          No eval report yet — run a retrieval benchmark to populate this view.
        </p>
      )}

      {evalReport.data && (
        <>
          <MetricTiles report={evalReport.data} />
          <section aria-label="Per-category metrics">
            <h3 className="niuu:text-xs niuu:uppercase niuu:tracking-[0.07em] niuu:text-text-muted niuu:m-0 niuu:mb-2">
              By category
            </h3>
            <CategoryTable byCategory={evalReport.data.byCategory} />
          </section>
        </>
      )}

      {/* ── Query stats ────────────────────────────────────────── */}
      <div className="niuu:border-t niuu:border-solid niuu:border-0 niuu:border-t-border niuu:pt-6">
        {queryStats.isLoading && (
          <div className={STATUS_ROW}>
            <StateDot state="processing" pulse />
            <span>loading query stats…</span>
          </div>
        )}

        {queryStats.isError && (
          <div className={STATUS_ROW}>
            <StateDot state="failed" />
            <span>
              {queryStats.error instanceof Error
                ? queryStats.error.message
                : 'query stats load failed'}
            </span>
          </div>
        )}

        {!queryStats.isLoading && !queryStats.isError && queryStats.data === null && (
          <p className="niuu:text-sm niuu:text-text-muted niuu:m-0" data-testid="queries-empty">
            No query statistics — this backend does not track search traffic.
          </p>
        )}

        {queryStats.data && <QueryTraffic stats={queryStats.data} />}
      </div>
    </div>
  );
}
