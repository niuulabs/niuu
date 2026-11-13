import { useId, useState, type FormEvent } from 'react';
import { Link, useSearch } from '@tanstack/react-router';
import { useMemorySearch } from '../../application/useMemoryGateway';
import { hitPlace, type MemoryHit } from '../../domain/memoryGateway';
import { COLUMN, ERROR, LINK, PAGE, QUIET } from './styles';

function HitTitle({ hit }: { hit: MemoryHit }) {
  if (hit.kind === 'page') {
    return (
      <Link
        to="/mimir/read"
        search={{ path: hit.ref, mount: hit.layer }}
        className={`${LINK} niuu:text-base niuu:font-medium`}
      >
        {hit.title}
      </Link>
    );
  }
  return (
    <span className="niuu:text-base niuu:font-medium niuu:text-text-primary">{hit.title}</span>
  );
}

function Hit({ hit }: { hit: MemoryHit }) {
  return (
    <li className="niuu:flex niuu:flex-col niuu:gap-1" data-testid="memory-hit">
      <HitTitle hit={hit} />
      {/* Shown three lines tall; the whole hit is one click away. */}
      <p className="niuu:m-0 niuu:line-clamp-3 niuu:break-words niuu:text-sm niuu:leading-relaxed niuu:text-text-secondary">
        {hit.snippet}
      </p>
      <p className="niuu:m-0 niuu:text-xs niuu:text-text-muted">
        {hitPlace(hit)}
        {hit.kind === 'source' ? <span>, {hit.ref}</span> : null}
      </p>
    </li>
  );
}

/** Search what the team knows: one box, and one list of hits from every memory the person reads. */
export function SearchPage() {
  const params = useSearch({ strict: false }) as { project?: string; q?: string };
  const id = useId();
  const project = params.project || undefined;
  const [text, setText] = useState(params.q ?? '');
  const [submitted, setSubmitted] = useState(params.q ?? '');
  const search = useMemorySearch(submitted, project);

  function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitted(text.trim());
  }

  const hits = search.data?.hits ?? [];

  return (
    <div className={PAGE} data-testid="memory-search-page">
      <form className={COLUMN} onSubmit={submit} role="search">
        <label htmlFor={id} className={QUIET}>
          Search what the team knows
        </label>
        <input
          id={id}
          type="text"
          value={text}
          autoFocus
          onChange={(event) => setText(event.target.value)}
          className="niuu:-mt-4 niuu:min-h-14 niuu:w-full niuu:rounded-xl niuu:border niuu:border-border niuu:bg-bg-secondary niuu:px-4 niuu:text-lg niuu:text-text-primary"
        />
        {search.isLoading ? <p className={QUIET}>Looking…</p> : null}
        {search.isError ? (
          <p className={ERROR} role="alert">
            {search.error instanceof Error ? search.error.message : String(search.error)}
          </p>
        ) : null}
        {search.data && hits.length === 0 ? (
          <p className={QUIET}>Nothing in memory matches that.</p>
        ) : null}
        {hits.length > 0 ? (
          <ul className="niuu:m-0 niuu:flex niuu:list-none niuu:flex-col niuu:gap-6 niuu:p-0">
            {hits.map((hit) => (
              <Hit key={`${hit.layer}/${hit.ref}`} hit={hit} />
            ))}
          </ul>
        ) : null}
      </form>
    </div>
  );
}
