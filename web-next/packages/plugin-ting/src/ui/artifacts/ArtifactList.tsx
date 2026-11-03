import './Artifacts.css';
import { useMemo, useState } from 'react';
import { Link } from '@tanstack/react-router';
import { ARTIFACT_STATUS_LABEL, type ArtifactStatus } from '../../domain/artifacts';
import { reviewPath, type ReviewItem } from '../../domain/review';
import { useReviews } from '../useWork';
import { useArtifacts, type ArtifactProject, type ArtifactRow } from '../useArtifacts';

/** One line of the list: an artifact, or a gate waiting on someone with nothing to read yet. */
interface ListRow {
  key: string;
  kind: string;
  title: string;
  byline: string;
  status: ArtifactStatus;
  project: ArtifactProject | null;
  updatedAt: string;
  href: string;
  search?: Record<string, string>;
}

const NO_PROJECT = 'No project';
const STATUS_ORDER: readonly ArtifactStatus[] = ['waiting', 'inProgress', 'done', 'failed'];

function artifactLine(row: ArtifactRow): ListRow {
  const versions = row.versions.length;
  const latest = row.versions[versions - 1];
  return {
    key: row.id,
    kind: row.title,
    title: row.run.title,
    byline: `${latest?.name ?? row.key}${versions > 1 ? ` · ${latest?.label} of ${versions} versions` : ''}`,
    status: row.status,
    project: row.project,
    updatedAt: row.updatedAt,
    href: `/ting/artifacts/${encodeURIComponent(row.run.slug)}`,
    search: { artifact: row.id },
  };
}

function gateLine(item: ReviewItem): ListRow {
  return {
    key: `gate:${item.taskId}`,
    kind: 'Decision',
    title: `${item.identifier ? `${item.identifier} · ` : ''}${item.title}`,
    byline: `${item.workflowName}${item.gateLabel ? ` · ${item.gateLabel}` : ''}`,
    status: 'waiting',
    project: item.projectId
      ? { id: item.projectId, name: item.projectName ?? item.projectId }
      : null,
    updatedAt: item.updatedAt,
    href: reviewPath(item),
  };
}

function age(iso: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.round(hours / 24)}d`;
}

function Row({ row }: { row: ListRow }) {
  return (
    <Link
      className="ting-artifacts__row"
      to={row.href as never}
      search={row.search as never}
      data-testid="artifact-row"
    >
      <span className="ting-artifacts__kind">{row.kind}</span>
      <span className="ting-artifacts__text">
        <span className="ting-artifacts__title">{row.title}</span>
        <span className="ting-artifacts__byline">{row.byline}</span>
      </span>
      <span className={`ting-artifacts__status ting-artifacts__status--${row.status}`}>
        {ARTIFACT_STATUS_LABEL[row.status]}
      </span>
      <time className="ting-artifacts__age" dateTime={row.updatedAt}>
        {age(row.updatedAt)}
      </time>
    </Link>
  );
}

/**
 * The one list of what agents produced, used wherever artifacts are shown: Work lists
 * everything, a project lists its own by passing `projectId`.
 */
export function ArtifactList({ projectId }: { projectId?: string }) {
  const artifacts = useArtifacts(projectId);
  const reviews = useReviews();
  const [status, setStatus] = useState<ArtifactStatus | null>(null);
  const [kind, setKind] = useState<string | null>(null);
  const [query, setQuery] = useState('');

  const rows = useMemo(() => {
    const withArtifacts = new Set(artifacts.rows.map((row) => row.run.slug));
    const waitingGates = (reviews.data?.items ?? [])
      .filter((item) => item.state === 'waiting' && !withArtifacts.has(item.taskId))
      .map(gateLine);
    return [...artifacts.rows.map(artifactLine), ...waitingGates]
      .filter((row) => !projectId || row.project?.id === projectId)
      .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));
  }, [artifacts.rows, reviews.data, projectId]);

  const kinds = [...new Set(rows.map((row) => row.kind))].sort();
  const needle = query.trim().toLowerCase();
  const shown = rows.filter(
    (row) =>
      (!status || row.status === status) &&
      (!kind || row.kind === kind) &&
      (!needle || `${row.title} ${row.byline}`.toLowerCase().includes(needle)),
  );
  const waiting = rows.filter((row) => row.status === 'waiting');
  const groups = new Map<string, ListRow[]>();
  for (const row of shown) {
    const name = row.project?.name ?? NO_PROJECT;
    groups.set(name, [...(groups.get(name) ?? []), row]);
  }

  return (
    <div className="ting-artifacts">
      <div className="ting-artifacts__filters" role="group" aria-label="Filter artifacts">
        <button type="button" aria-pressed={status === null} onClick={() => setStatus(null)}>
          All <span>{rows.length}</span>
        </button>
        {STATUS_ORDER.map((value) => {
          const count = rows.filter((row) => row.status === value).length;
          if (count === 0) return null;
          return (
            <button
              key={value}
              type="button"
              aria-pressed={status === value}
              onClick={() => setStatus(status === value ? null : value)}
            >
              {ARTIFACT_STATUS_LABEL[value]} <span>{count}</span>
            </button>
          );
        })}
        <label className="ting-artifacts__kind-filter">
          <span>Kind</span>
          <select value={kind ?? ''} onChange={(event) => setKind(event.target.value || null)}>
            <option value="">Any</option>
            {kinds.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <input
          type="search"
          className="ting-artifacts__search"
          aria-label="Search artifacts"
          placeholder="Search titles"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>

      {artifacts.error ? (
        <p role="alert" className="ting-artifacts__alert">
          {artifacts.error instanceof Error
            ? artifacts.error.message
            : 'Artifacts could not be read.'}
        </p>
      ) : null}
      {artifacts.unreadable.length > 0 ? (
        <details role="alert" className="ting-artifacts__alert">
          <summary>
            The artifacts of {artifacts.unreadable.length} Forge host
            {artifacts.unreadable.length === 1 ? '' : 's'} could not be read, so the runs there are
            not listed.
          </summary>
          <ul>
            {artifacts.unreadable.map(({ host, reason }) => (
              <li key={host ?? 'default'}>
                {host ?? 'This host'}: {reason}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {artifacts.loading ? (
        <p role="status" className="ting-artifacts__progress">
          Reading artifacts…
        </p>
      ) : null}

      {status === null && waiting.length > 0 ? (
        <section className="ting-artifacts__waiting" aria-label="Waiting on you">
          <h2>Waiting on you · {waiting.length}</h2>
          {waiting.map((row) => (
            <Row key={row.key} row={row} />
          ))}
        </section>
      ) : null}

      {[...groups.entries()].map(([name, inGroup]) => (
        <section key={name} className="ting-artifacts__group" aria-label={name}>
          {projectId ? null : (
            <h2>
              {name} <span>{inGroup.length}</span>
            </h2>
          )}
          <div className="ting-artifacts__rows">
            {inGroup.map((row) => (
              <Row key={row.key} row={row} />
            ))}
          </div>
        </section>
      ))}

      {!artifacts.loading && shown.length === 0 ? (
        <p className="ting-artifacts__empty">
          {rows.length === 0
            ? 'No run has presented a document yet.'
            : 'Nothing matches these filters.'}
        </p>
      ) : null}
    </div>
  );
}
