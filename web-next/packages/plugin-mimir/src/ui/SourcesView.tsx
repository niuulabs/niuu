/**
 * SourcesView — raw ingest records filtered by origin type.
 *
 * Layout (matches web2 IngestView):
 *   Ingest form (URL | File toggle, URL input + Fetch, File dropzone)
 *   Origin filter tabs
 *   Source count + table
 *
 * Each row shows: origin badge, title, URL/path, ingest agent + date,
 * and the pages it was compiled into.
 */

import { useState, useRef } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { StateDot, Table } from '@niuulabs/ui';
import type { TableColumn } from '@niuulabs/ui';
import { useService } from '@niuulabs/plugin-sdk';
import { useMimirSources } from './useMimirSources';
import type { Source } from '../domain/source';
import type { OriginType } from '../domain/source';
import type { IMimirService } from '../ports';
import './mimir-views.css';

type IngestMode = 'url' | 'file';

const ORIGINS: Array<{ id: OriginType | 'all'; label: string }> = [
  { id: 'all', label: 'all' },
  { id: 'web', label: 'web' },
  { id: 'rss', label: 'rss' },
  { id: 'arxiv', label: 'arxiv' },
  { id: 'file', label: 'file' },
  { id: 'mail', label: 'mail' },
  { id: 'chat', label: 'chat' },
];

const ORIGIN_BADGE_CLASS: Record<OriginType, string> = {
  web: 'mm-origin-badge mm-origin-badge--web',
  rss: 'mm-origin-badge mm-origin-badge--rss',
  arxiv: 'mm-origin-badge mm-origin-badge--arxiv',
  file: 'mm-origin-badge mm-origin-badge--file',
  mail: 'mm-origin-badge mm-origin-badge--mail',
  chat: 'mm-origin-badge mm-origin-badge--chat',
};

function formatDate(iso: string): string {
  return iso.slice(0, 10);
}

const SOURCES_COLUMNS: TableColumn<Source>[] = [
  {
    key: 'id',
    header: 'ID / Origin',
    width: '140px',
    render: (row) => (
      <div>
        <div className="niuu-font-mono niuu-text-xs niuu-text-text-muted">
          {row.id.slice(0, 10)}
        </div>
        <span
          className={ORIGIN_BADGE_CLASS[row.originType]}
          aria-label={`origin: ${row.originType}`}
        >
          {row.originType}
        </span>
      </div>
    ),
  },
  {
    key: 'title',
    header: 'Title',
    render: (row) => (
      <div>
        <div className="niuu-text-sm niuu-text-text-primary">{row.title}</div>
        <p className="niuu-text-xs niuu-text-text-muted niuu-m-0 niuu-pt-0.5">
          ingested {formatDate(row.ingestedAt)} · {row.ingestAgent}
        </p>
      </div>
    ),
  },
  {
    key: 'origin',
    header: 'Origin / Path',
    render: (row) => {
      if (row.originUrl) {
        return (
          <span
            className="niuu-font-mono niuu-text-xs niuu-text-brand-300 niuu-overflow-hidden niuu-text-ellipsis niuu-whitespace-nowrap niuu-block"
            title={row.originUrl}
          >
            {row.originUrl}
          </span>
        );
      }
      if (row.originPath) {
        return (
          <span className="niuu-font-mono niuu-text-xs niuu-text-text-muted">{row.originPath}</span>
        );
      }
      return <span className="niuu-text-text-muted niuu-italic niuu-text-xs">—</span>;
    },
  },
  {
    key: 'compiledInto',
    header: 'Compiled into',
    render: (row) => {
      if (row.compiledInto.length === 0) {
        return (
          <span className="niuu-text-text-muted niuu-italic niuu-text-xs">not compiled yet</span>
        );
      }
      return (
        <div className="niuu-flex niuu-flex-wrap niuu-gap-1">
          {row.compiledInto.map((path) => (
            <span key={path} className="mm-source-path-chip">
              {path}
            </span>
          ))}
        </div>
      );
    },
  },
];

// ---------------------------------------------------------------------------
// Ingest form
// ---------------------------------------------------------------------------

interface IngestFormProps {
  onIngestSuccess: () => void;
  onMutationStart: () => void;
}

function IngestForm({ onIngestSuccess, onMutationStart }: IngestFormProps) {
  const [mode, setMode] = useState<IngestMode>('url');
  const [url, setUrl] = useState('');
  const [ingestError, setIngestError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const service = useService<IMimirService>('mimir');
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: (payload: { type: 'url'; url: string } | { type: 'file'; file: File }) => {
      onMutationStart();
      if (payload.type === 'url') {
        return service.pages.ingestUrl(payload.url);
      }
      return service.pages.ingestFile(payload.file);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['mimir', 'sources'] });
      setUrl('');
      setIngestError(null);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
      onIngestSuccess();
    },
    onError: (err: unknown) => {
      setIngestError(err instanceof Error ? err.message : 'ingest failed');
    },
  });

  function handleFetch(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim()) return;
    setIngestError(null);
    mutation.mutate({ type: 'url', url: url.trim() });
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setIngestError(null);
    mutation.mutate({ type: 'file', file });
  }

  const isPending = mutation.isPending;

  return (
    <section
      className="niuu-bg-bg-secondary niuu-border niuu-border-border-subtle niuu-rounded-lg niuu-p-4 niuu-flex niuu-flex-col niuu-gap-3"
      aria-label="Ingest source"
    >
      <div className="niuu-flex niuu-items-center niuu-justify-between">
        <span className="niuu-text-xs niuu-text-text-muted niuu-uppercase niuu-tracking-widest">
          Ingest
        </span>

        {/* Mode toggle */}
        <div
          className="niuu-flex niuu-rounded-md niuu-overflow-hidden niuu-border niuu-border-border-subtle"
          role="group"
          aria-label="Ingest mode"
        >
          <button
            type="button"
            className={[
              'niuu-px-3 niuu-py-1 niuu-text-xs niuu-font-mono niuu-border-r niuu-border-border-subtle niuu-transition-colors',
              mode === 'url'
                ? 'niuu-bg-bg-tertiary niuu-text-text-primary'
                : 'niuu-bg-bg-secondary niuu-text-text-muted hover:niuu-text-text-secondary',
            ].join(' ')}
            aria-pressed={mode === 'url'}
            onClick={() => setMode('url')}
            data-testid="mode-url"
          >
            URL
          </button>
          <button
            type="button"
            className={[
              'niuu-px-3 niuu-py-1 niuu-text-xs niuu-font-mono niuu-transition-colors',
              mode === 'file'
                ? 'niuu-bg-bg-tertiary niuu-text-text-primary'
                : 'niuu-bg-bg-secondary niuu-text-text-muted hover:niuu-text-text-secondary',
            ].join(' ')}
            aria-pressed={mode === 'file'}
            onClick={() => setMode('file')}
            data-testid="mode-file"
          >
            File
          </button>
        </div>
      </div>

      {mode === 'url' && (
        <form onSubmit={handleFetch} className="niuu-flex niuu-gap-2" aria-label="Fetch URL">
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://example.com/article"
            disabled={isPending}
            required
            aria-label="URL to fetch"
            data-testid="url-input"
            className="niuu-flex-1 niuu-bg-bg-primary niuu-border niuu-border-border niuu-rounded-sm niuu-px-3 niuu-py-1 niuu-text-sm niuu-text-text-primary niuu-font-mono placeholder:niuu-text-text-muted focus:niuu-outline-none focus:niuu-border-brand-300"
          />
          <button
            type="submit"
            disabled={isPending || !url.trim()}
            aria-label="Fetch"
            data-testid="fetch-button"
            className="niuu-px-4 niuu-py-1 niuu-text-xs niuu-rounded-sm niuu-bg-brand niuu-border niuu-border-brand niuu-text-bg-primary niuu-font-mono niuu-font-medium disabled:niuu-opacity-50 hover:niuu-opacity-85 niuu-transition-opacity"
          >
            {isPending ? 'Fetching…' : 'Fetch'}
          </button>
        </form>
      )}

      {mode === 'file' && (
        <label
          className="niuu-flex niuu-flex-col niuu-items-center niuu-justify-center niuu-gap-2 niuu-border niuu-border-dashed niuu-border-border niuu-rounded-md niuu-p-6 niuu-text-center niuu-cursor-pointer hover:niuu-border-brand-300 niuu-transition-colors"
          aria-label="Upload file dropzone"
          data-testid="file-dropzone"
        >
          <span className="niuu-text-text-muted niuu-text-sm">
            {isPending ? 'Uploading…' : 'Drop a file here or click to browse'}
          </span>
          <span className="niuu-text-text-muted niuu-text-xs">Markdown, plain text, PDF</span>
          <input
            ref={fileInputRef}
            type="file"
            className="niuu-sr-only"
            onChange={handleFileChange}
            disabled={isPending}
            accept=".md,.txt,.pdf,.html"
            data-testid="file-input"
          />
        </label>
      )}

      {isPending && (
        <div className="niuu-flex niuu-items-center niuu-gap-2">
          <StateDot state="processing" pulse />
          <span className="niuu-text-xs niuu-text-text-secondary">
            {mode === 'url' ? 'Fetching source…' : 'Uploading file…'}
          </span>
        </div>
      )}

      {ingestError && (
        <p className="niuu-text-xs niuu-text-critical niuu-m-0" role="alert">
          {ingestError}
        </p>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// SourcesView
// ---------------------------------------------------------------------------

export function SourcesView() {
  const [activeOrigin, setActiveOrigin] = useState<OriginType | 'all'>('all');
  const [showSuccess, setShowSuccess] = useState(false);

  const {
    data: sources,
    isLoading,
    isError,
    error,
  } = useMimirSources(activeOrigin !== 'all' ? { originType: activeOrigin } : undefined);

  function handleIngestSuccess() {
    setShowSuccess(true);
    setTimeout(() => setShowSuccess(false), 3000);
  }

  return (
    <div className="niuu-p-6 niuu-flex niuu-flex-col niuu-gap-4">
      {/* ── Ingest form ────────────────────────────────────────── */}
      <IngestForm
        onIngestSuccess={handleIngestSuccess}
        onMutationStart={() => setShowSuccess(false)}
      />

      {showSuccess && (
        <p
          className="niuu-text-xs niuu-text-brand-200 niuu-m-0"
          role="status"
          data-testid="ingest-success"
        >
          Source ingested successfully.
        </p>
      )}

      {/* ── Origin filter tabs ─────────────────────────────────── */}
      <div
        className="niuu-flex niuu-gap-2 niuu-flex-wrap"
        role="tablist"
        aria-label="filter by origin"
      >
        {ORIGINS.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={activeOrigin === id}
            className={[
              'niuu-px-3 niuu-py-1 niuu-rounded-full niuu-text-xs niuu-font-mono niuu-border niuu-transition-colors',
              activeOrigin === id
                ? 'niuu-border-brand-300/30 niuu-text-brand-300 mm-origin-chip--active-bg'
                : 'niuu-border-border niuu-bg-bg-secondary niuu-text-text-secondary hover:niuu-bg-bg-tertiary hover:niuu-text-text-primary',
            ].join(' ')}
            onClick={() => setActiveOrigin(id as OriginType | 'all')}
          >
            {label}
          </button>
        ))}
      </div>

      {/* ── Status ─────────────────────────────────────────────── */}
      {isLoading && (
        <div className="niuu-flex niuu-items-center niuu-gap-2">
          <StateDot state="processing" pulse />
          <span className="niuu-text-sm niuu-text-text-secondary">loading sources…</span>
        </div>
      )}

      {isError && (
        <p className="niuu-text-sm niuu-text-critical niuu-m-0">
          {error instanceof Error ? error.message : 'failed to load sources'}
        </p>
      )}

      {/* ── Source table ───────────────────────────────────────── */}
      {sources && (
        <>
          <p className="niuu-text-xs niuu-text-text-muted niuu-m-0">
            {sources.length} source{sources.length !== 1 ? 's' : ''}
            {activeOrigin !== 'all' ? ` · origin: ${activeOrigin}` : ''}
          </p>
          <Table<Source> columns={SOURCES_COLUMNS} rows={sources} aria-label="sources table" />
        </>
      )}
    </div>
  );
}
