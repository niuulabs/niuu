import { useState } from 'react';
import type { Meta, StoryObj } from '@storybook/react';
import { Table, type TableColumn, type SortDir } from './Table';

interface Session {
  id: string;
  name: string;
  status: 'running' | 'idle' | 'error';
  duration: string;
  agent: string;
}

const SESSIONS: Session[] = [
  { id: 's1', name: 'dispatch-prod-001', status: 'running', duration: '4m 12s', agent: 'Skoll' },
  { id: 's2', name: 'dispatch-prod-002', status: 'idle', duration: '—', agent: 'Hati' },
  { id: 's3', name: 'dispatch-dev-010', status: 'error', duration: '0m 03s', agent: 'Modi' },
  { id: 's4', name: 'dispatch-prod-003', status: 'running', duration: '12m 01s', agent: 'Skoll' },
];

const COLUMNS: TableColumn<Session>[] = [
  { key: 'name', header: 'Name', render: (r) => <code>{r.name}</code>, sortable: true },
  { key: 'status', header: 'Status', render: (r) => r.status, sortable: true },
  { key: 'duration', header: 'Duration', render: (r) => r.duration },
  { key: 'agent', header: 'Agent', render: (r) => r.agent, sortable: true },
];

const meta: Meta = {
  title: 'Data/Table',
};
export default meta;

export const Basic: StoryObj = {
  render: () => <Table columns={COLUMNS} rows={SESSIONS} aria-label="Sessions" />,
};

function SortingDemo() {
  const [sortKey, setSortKey] = useState<string>('name');
  const [sortDir, setSortDir] = useState<SortDir>('asc');

  const sorted = [...SESSIONS].sort((a, b) => {
    const va = a[sortKey as keyof Session] ?? '';
    const vb = b[sortKey as keyof Session] ?? '';
    const cmp = va < vb ? -1 : va > vb ? 1 : 0;
    return sortDir === 'asc' ? cmp : -cmp;
  });

  return (
    <Table
      columns={COLUMNS}
      rows={sorted}
      sortKey={sortKey}
      sortDir={sortDir}
      onSort={(k, d) => {
        setSortKey(k);
        setSortDir(d);
      }}
      aria-label="Sortable sessions"
    />
  );
}

export const WithSorting: StoryObj = {
  render: () => <SortingDemo />,
};

function SelectionDemo() {
  const [selected, setSelected] = useState<Set<string | number>>(new Set());
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <Table
        columns={COLUMNS}
        rows={SESSIONS}
        selectedIds={selected}
        onSelectionChange={setSelected}
        aria-label="Selectable sessions"
      />
      <pre style={{ fontSize: 11 }}>Selected: {JSON.stringify([...selected])}</pre>
    </div>
  );
}

export const WithSelection: StoryObj = {
  render: () => <SelectionDemo />,
};

function ExpandDemo() {
  const [expandedId, setExpandedId] = useState<string | number | null>(null);
  return (
    <Table
      columns={COLUMNS}
      rows={SESSIONS}
      expandedId={expandedId}
      onExpandChange={setExpandedId}
      getExpandedContent={(row) => (
        <div style={{ padding: '8px 0', fontFamily: 'monospace', fontSize: 12 }}>
          <strong>ID:</strong> {row.id} · <strong>Status:</strong> {row.status} ·{' '}
          <strong>Duration:</strong> {row.duration}
        </div>
      )}
      aria-label="Expandable sessions"
    />
  );
}

export const WithRowExpand: StoryObj = {
  render: () => <ExpandDemo />,
};

function FullFeaturedDemo() {
  const [sortKey, setSortKey] = useState<string>('name');
  const [sortDir, setSortDir] = useState<SortDir>('asc');
  const [selected, setSelected] = useState<Set<string | number>>(new Set());
  const [expandedId, setExpandedId] = useState<string | number | null>(null);

  const sorted = [...SESSIONS].sort((a, b) => {
    const va = a[sortKey as keyof Session] ?? '';
    const vb = b[sortKey as keyof Session] ?? '';
    const cmp = va < vb ? -1 : va > vb ? 1 : 0;
    return sortDir === 'asc' ? cmp : -cmp;
  });

  return (
    <Table
      columns={COLUMNS}
      rows={sorted}
      sortKey={sortKey}
      sortDir={sortDir}
      onSort={(k, d) => {
        setSortKey(k);
        setSortDir(d);
      }}
      selectedIds={selected}
      onSelectionChange={setSelected}
      expandedId={expandedId}
      onExpandChange={setExpandedId}
      getExpandedContent={(row) => (
        <div style={{ fontFamily: 'monospace', fontSize: 12 }}>Full detail for {row.name}</div>
      )}
      aria-label="Full featured sessions table"
    />
  );
}

export const FullFeatured: StoryObj = {
  render: () => <FullFeaturedDemo />,
};
