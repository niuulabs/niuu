/**
 * AskMemoryPage — one question, the pages that answer it, and the facts those
 * pages actually carry.
 *
 * The right-hand panel quotes Key Facts verbatim with the page they came from.
 * Nothing on this screen is composed, summarised or rephrased: if a sentence
 * is not on a page, it is not here.
 */

import { useMemo } from 'react';
import { Link, useNavigate, useSearch } from '@tanstack/react-router';
import { ArrowLeft, MessageSquare, TriangleAlert } from 'lucide-react';
import { Chip, EmptyState, ErrorState, LoadingState } from '@niuulabs/ui';
import type { Page, SearchResult } from '../../domain/page';
import { isWeakening, type FactEvidence } from '../../domain/evidence';
import { factsOf, quoteKeyFacts, type QuotedFact } from '../../domain/quoteFacts';
import { useMimirMounts } from '../useMimirMounts';
import { MountChip } from '../components/MountChip';
import { PageTypeGlyph } from '../components/PageTypeGlyph';
import { AskBox } from './AskBox';
import { ProofPill } from './ProofPill';
import { ReviseFact } from './ReviseFact';
import { useEvidenceForPaths, useMemorySearch, usePagesForPaths } from './useMemory';

/** How many answering pages the facts panel quotes from. */
const QUOTED_PAGES = 3;

const EXAMPLE_QUESTIONS = [
  'how do we deploy to Kubernetes?',
  'what did we decide about migrations?',
  'which model does the resident run?',
];

const SECTION =
  'niuu:flex niuu:flex-col niuu:gap-3 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-5 niuu:py-4';
const REALM_MOUNT_PREFIX = 'realm-';

interface AskSearch {
  q?: string;
  mount?: string;
  path?: string;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

// ---------------------------------------------------------------------------
// Result card
// ---------------------------------------------------------------------------

interface ResultCardProps {
  result: SearchResult;
  page: Page | undefined;
  evidence: FactEvidence[];
  selected: boolean;
  onSelect: (path: string) => void;
}

function ResultCard({ result, page, evidence, selected, onSelect }: ResultCardProps) {
  const facts = page ? factsOf(page) : [];
  const weakest = evidence.find(isWeakening) ?? null;

  return (
    <button
      type="button"
      onClick={() => onSelect(result.path)}
      aria-pressed={selected}
      data-testid="memory-result"
      className={[
        'niuu:flex niuu:w-full niuu:flex-col niuu:gap-1.5 niuu:rounded-xl niuu:border niuu:px-4 niuu:py-3 niuu:text-left',
        selected
          ? 'niuu:border-brand/50 niuu:bg-brand/10'
          : 'niuu:border-border-subtle niuu:bg-bg-secondary',
      ].join(' ')}
    >
      <div className="niuu:flex niuu:items-baseline niuu:gap-3">
        <PageTypeGlyph type={result.type} />
        <span className="niuu:flex-1 niuu:text-sm niuu:font-medium niuu:text-text-primary">
          {result.title}
        </span>
        {typeof result.score === 'number' ? (
          <span className="niuu:font-mono niuu:text-[10px] niuu:text-text-faint">
            {result.score.toFixed(2)}
          </span>
        ) : null}
      </div>
      <span className="niuu:font-mono niuu:text-[10px] niuu:text-text-muted">{result.path}</span>
      <p className="niuu:m-0 niuu:text-xs niuu:leading-normal niuu:text-text-secondary">
        {result.summary}
      </p>
      <div className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-1.5">
        {(result.mounts ?? []).map((mount) => (
          <MountChip key={mount} name={mount} />
        ))}
        {page ? (
          <Chip tone="muted">
            {facts.length} {facts.length === 1 ? 'fact' : 'facts'}
          </Chip>
        ) : null}
        {weakest ? <ProofPill evidence={weakest} /> : null}
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function AskMemoryPage() {
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as AskSearch;
  const question = search.q ?? '';
  const mount = search.mount;
  const mounts = useMimirMounts();
  const results = useMemorySearch(question, mount);

  const rows = useMemo(() => results.data ?? [], [results.data]);
  const quotedPaths = useMemo(() => rows.slice(0, QUOTED_PAGES).map((row) => row.path), [rows]);
  const pages = usePagesForPaths(quotedPaths, mount);
  const { byPath: evidenceByPath } = useEvidenceForPaths(quotedPaths);

  const selectedPath = search.path ?? rows[0]?.path;
  const selected = rows.find((row) => row.path === selectedPath) ?? rows[0];

  const quotedPages = useMemo(
    () => quotedPaths.map((path) => pages.get(path)).filter((p): p is Page => Boolean(p)),
    [quotedPaths, pages],
  );

  const quoted: QuotedFact[] = useMemo(
    () => quoteKeyFacts(quotedPages, evidenceByPath),
    [quotedPages, evidenceByPath],
  );

  const weakQuoted = quoted.find((entry) => entry.evidence && isWeakening(entry.evidence));
  const selectedMount = selected?.mounts?.[0] ?? mount;
  const residentRealm = selectedMount?.startsWith(REALM_MOUNT_PREFIX)
    ? selectedMount.slice(REALM_MOUNT_PREFIX.length)
    : null;

  function ask(next: string) {
    void navigate({ to: '/mimir/ask', search: { q: next, mount } });
  }

  function chooseMount(next?: string) {
    void navigate({ to: '/mimir/ask', search: { q: question, mount: next } });
  }

  function select(path: string) {
    void navigate({ to: '/mimir/ask', search: { q: question, mount, path } });
  }

  const searchedLabel = mount ?? 'every mount';

  return (
    <div
      className="niuu:flex niuu:flex-col niuu:gap-5 niuu:px-10 niuu:py-6"
      data-testid="memory-ask-page"
    >
      <Link
        to="/mimir"
        className="niuu:inline-flex niuu:items-center niuu:gap-1.5 niuu:text-[11px] niuu:text-text-muted niuu:hover:text-text-primary"
      >
        <ArrowLeft size={12} aria-hidden="true" />
        Memory
      </Link>

      <AskBox value={question} onAsk={ask} autoFocus={question.length === 0} />

      <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-4">
        <span className="niuu:text-[11px] niuu:text-text-muted">
          {question ? (
            <>
              <span className="niuu:text-text-primary">{rows.length}</span>{' '}
              {rows.length === 1 ? 'page answers' : 'pages answer'} · hybrid (keyword + meaning) ·
              searched: {searchedLabel}
            </>
          ) : (
            'nothing asked yet'
          )}
        </span>
        <div className="niuu:flex niuu:flex-wrap niuu:justify-end niuu:gap-1.5">
          <button
            type="button"
            onClick={() => chooseMount(undefined)}
            aria-pressed={!mount}
            data-testid="memory-mount-filter-all"
          >
            <Chip tone={mount ? 'muted' : 'brand'}>all mounts</Chip>
          </button>
          {(mounts.data ?? []).map((entry) => (
            <button
              key={entry.name}
              type="button"
              onClick={() => chooseMount(entry.name)}
              aria-pressed={mount === entry.name}
              data-testid={`memory-mount-filter-${entry.name}`}
            >
              <Chip tone={mount === entry.name ? 'brand' : 'muted'}>{entry.name}</Chip>
            </button>
          ))}
        </div>
      </div>

      {question.length === 0 ? (
        <EmptyState
          title="Ask memory a question"
          description="It answers from the pages residents wrote — with the facts they carry and the proof behind them."
          action={
            <div className="niuu:flex niuu:flex-col niuu:gap-1.5">
              {EXAMPLE_QUESTIONS.map((example) => (
                <button
                  key={example}
                  type="button"
                  className="niuu:text-xs niuu:text-brand-300"
                  onClick={() => ask(example)}
                >
                  {example}
                </button>
              ))}
            </div>
          }
        />
      ) : null}

      {results.isLoading ? <LoadingState label="Searching memory…" /> : null}
      {results.isError ? (
        <ErrorState message={errorMessage(results.error, 'Search failed')} />
      ) : null}
      {question.length > 0 && !results.isLoading && !results.isError && rows.length === 0 ? (
        <EmptyState
          title="No page answers that yet"
          description="Nothing written so far matches the question. Ask a resident to look into it."
        />
      ) : null}

      {rows.length > 0 ? (
        <div className="niuu:grid niuu:grid-cols-[1.25fr_1fr] niuu:items-start niuu:gap-6">
          <div className="niuu:flex niuu:flex-col niuu:gap-2.5" data-testid="memory-results">
            {rows.map((row) => (
              <ResultCard
                key={row.path}
                result={row}
                page={pages.get(row.path)}
                evidence={evidenceByPath.get(row.path) ?? []}
                selected={row.path === selected?.path}
                onSelect={select}
              />
            ))}
          </div>

          <section
            className={`${SECTION} niuu:border-brand/40`}
            data-testid="memory-facts"
            aria-label="the facts behind the answer"
          >
            <div className="niuu:flex niuu:flex-col niuu:gap-0.5">
              <h2 className="niuu:text-sm niuu:font-medium niuu:text-text-primary">
                The facts behind the answer
              </h2>
              <span className="niuu:text-[11px] niuu:text-text-muted">
                quoted from the pages on the left, as written · nothing composed
              </span>
            </div>

            {quoted.length === 0 ? (
              <p className="niuu:text-xs niuu:text-text-faint">
                The answering pages carry no written facts yet.
              </p>
            ) : null}

            {quoted.map((entry) => (
              <div
                key={`${entry.page.path}-${entry.position}`}
                className="niuu:flex niuu:flex-col niuu:gap-1 niuu:border-b niuu:border-border-subtle niuu:py-2 niuu:last:border-b-0"
              >
                <div className="niuu:flex niuu:items-start niuu:gap-2.5">
                  <span className="niuu:mt-1.5 niuu:size-1.5 niuu:shrink-0 niuu:rounded-full niuu:bg-brand-300" />
                  <span className="niuu:flex-1 niuu:text-xs niuu:leading-relaxed niuu:text-text-primary">
                    {entry.fact}{' '}
                    <span className="niuu:font-mono niuu:text-[10px] niuu:text-text-faint">
                      {entry.page.title} · fact {entry.position}
                    </span>
                  </span>
                </div>
                {entry.evidence ? (
                  <div className="niuu:pl-4">
                    <ProofPill evidence={entry.evidence} />
                  </div>
                ) : null}
              </div>
            ))}

            {weakQuoted ? (
              <div
                className="niuu:flex niuu:flex-col niuu:gap-2 niuu:rounded-lg niuu:border niuu:border-status-amber/50 niuu:bg-status-amber/10 niuu:px-3 niuu:py-2.5"
                data-testid="memory-facts-warning"
              >
                <span className="niuu:flex niuu:items-center niuu:gap-1.5 niuu:text-[11px] niuu:text-status-amber">
                  <TriangleAlert size={12} aria-hidden="true" />
                  One fact behind this answer is weakening
                </span>
                <ReviseFact path={weakQuoted.page.path} fact={weakQuoted.fact} index={0} />
              </div>
            ) : null}

            <div className="niuu:flex niuu:items-center niuu:gap-3 niuu:border-t niuu:border-border-subtle niuu:pt-3">
              <Link
                to={residentRealm ? `/realms/${residentRealm}` : '/ravn/residents'}
                className="niuu:inline-flex niuu:items-center niuu:gap-1.5 niuu:text-[11px] niuu:text-text-muted niuu:hover:text-text-primary"
                data-testid="memory-ask-resident"
              >
                <MessageSquare size={12} aria-hidden="true" />
                Ask the resident
              </Link>
              {selected ? (
                <Link
                  to="/mimir/read"
                  search={{ path: selected.path, mount: selected.mounts?.[0] ?? mount }}
                  className="niuu:ml-auto niuu:text-[11px] niuu:text-brand-300"
                  data-testid="memory-open-page"
                >
                  Open {selected.title} ›
                </Link>
              ) : null}
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
