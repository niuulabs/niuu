import { useState } from 'react';
import {
  Table,
  type TableColumn,
  type SortDir,
  FilterBar,
  FilterToggle,
  type FilterState,
  KpiStrip,
  KpiCard,
  EmptyState,
  LoadingState,
  ErrorState,
} from '@niuulabs/ui';
import './ShowcasePage.css';

interface DispatchRow {
  id: string;
  name: string;
  status: 'running' | 'idle' | 'error';
  agent: string;
  duration: string;
}

const ALL_ROWS: DispatchRow[] = [
  { id: 'd1', name: 'dispatch-prod-001', status: 'running', agent: 'Skoll', duration: '4m 12s' },
  { id: 'd2', name: 'dispatch-prod-002', status: 'idle', agent: 'Hati', duration: '—' },
  { id: 'd3', name: 'dispatch-dev-010', status: 'error', agent: 'Móði', duration: '0m 03s' },
  { id: 'd4', name: 'dispatch-prod-003', status: 'running', agent: 'Skoll', duration: '12m 01s' },
  { id: 'd5', name: 'dispatch-stg-007', status: 'idle', agent: 'Víðarr', duration: '—' },
];

const COLUMNS: TableColumn<DispatchRow>[] = [
  { key: 'name', header: 'Name', render: (r) => <code>{r.name}</code>, sortable: true },
  { key: 'status', header: 'Status', render: (r) => r.status, sortable: true },
  { key: 'agent', header: 'Agent', render: (r) => r.agent, sortable: true },
  { key: 'duration', header: 'Duration', render: (r) => r.duration },
];

type DataMode = 'data' | 'loading' | 'empty' | 'error';

export function ShowcasePage() {
  const [filters, setFilters] = useState<FilterState>({ q: '' });
  const [activeOnly, setActiveOnly] = useState(false);
  const [sortKey, setSortKey] = useState<string>('name');
  const [sortDir, setSortDir] = useState<SortDir>('asc');
  const [selectedIds, setSelectedIds] = useState<Set<string | number>>(new Set());
  const [expandedId, setExpandedId] = useState<string | number | null>(null);
  const [mode, setMode] = useState<DataMode>('data');

  const query = filters.q ?? '';
  const filtered = ALL_ROWS.filter((r) => {
    if (activeOnly && r.status !== 'running') return false;
    if (query && !r.name.includes(query) && !r.agent.includes(query)) return false;
    return true;
  });

  const sorted = [...filtered].sort((a, b) => {
    const va = a[sortKey as keyof DispatchRow] ?? '';
    const vb = b[sortKey as keyof DispatchRow] ?? '';
    const cmp = va < vb ? -1 : va > vb ? 1 : 0;
    return sortDir === 'asc' ? cmp : -cmp;
  });

  return (
    <div className="showcase-page">
      <h2 className="showcase-page__heading">NIU-658 · Data surfaces showcase</h2>

      {/* KpiStrip */}
      <section aria-label="KPI metrics">
        <KpiStrip>
          <KpiCard
            label="Total Dispatches"
            value="1,204"
            delta="+8%"
            deltaTrend="up"
            deltaLabel="vs last week"
          />
          <KpiCard label="Running" value={42} delta="+3" deltaTrend="up" />
          <KpiCard label="Error Rate" value="0.4%" delta="-0.1%" deltaTrend="up" />
          <KpiCard label="Avg Duration" value="4m 12s" delta="±2s" deltaTrend="neutral" />
        </KpiStrip>
      </section>

      {/* Mode switcher */}
      <div className="showcase-mode-switcher">
        {(['data', 'loading', 'empty', 'error'] as DataMode[]).map((m) => (
          <button
            key={m}
            data-testid={`mode-${m}`}
            onClick={() => setMode(m)}
            className={`showcase-mode-btn${mode === m ? ' showcase-mode-btn--active' : ''}`}
          >
            {m}
          </button>
        ))}
      </div>

      {/* FilterBar */}
      <FilterBar value={filters} onChange={setFilters} placeholder="Search dispatches…">
        <FilterToggle label="Running only" active={activeOnly} onChange={setActiveOnly} />
      </FilterBar>

      {/* Table / states */}
      {mode === 'loading' && <LoadingState label="Loading dispatches…" />}
      {mode === 'empty' && (
        <EmptyState
          icon="📭"
          title="No dispatches found"
          description="Try adjusting your filters or create a new dispatch."
          action={<button className="showcase-action-btn">New Dispatch</button>}
        />
      )}
      {mode === 'error' && (
        <ErrorState
          icon="⚠️"
          title="Could not load dispatches"
          message="The Tyr service returned a 503. Check your connection and retry."
          action={
            <button className="showcase-action-btn showcase-action-btn--secondary">Retry</button>
          }
        />
      )}
      {mode === 'data' && (
        <Table
          columns={COLUMNS}
          rows={sorted}
          sortKey={sortKey}
          sortDir={sortDir}
          onSort={(k, d) => {
            setSortKey(k);
            setSortDir(d);
          }}
          selectedIds={selectedIds}
          onSelectionChange={setSelectedIds}
          expandedId={expandedId}
          onExpandChange={setExpandedId}
          getExpandedContent={(row) => (
            <div className="showcase-expand-detail">
              <strong>ID:</strong> {row.id} &nbsp;·&nbsp; <strong>Status:</strong> {row.status}{' '}
              &nbsp;·&nbsp; <strong>Duration:</strong> {row.duration}
            </div>
          )}
          aria-label="Dispatch table"
        />
      )}
    </div>
  );
}
