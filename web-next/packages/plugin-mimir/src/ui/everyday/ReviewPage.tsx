import { useState, type ReactNode } from 'react';
import { useSearch } from '@tanstack/react-router';
import { useDecideProposal, useMemoryReview } from '../../application/useMemoryGateway';
import { sourceLabel, type MemoryProposal, type ReviewSource } from '../../domain/memoryGateway';
import { COLUMN, ERROR, LABEL, LINK, PAGE, PRIMARY, QUIET, SECONDARY } from './styles';

function Part({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-1.5">
      <div className={LABEL}>{label}</div>
      {children}
    </div>
  );
}

function Source({ source }: { source: ReviewSource }) {
  const label = sourceLabel(source);
  if (!source.url) return <>{label}</>;
  return (
    <a className={LINK} href={source.url} target="_blank" rel="noreferrer">
      {label}
    </a>
  );
}

/** "An agent picked it up from LIN-501 and its work in a session." */
function Origin({ proposal }: { proposal: MemoryProposal }) {
  const who = proposal.by === 'agent' ? 'An agent' : 'A person';
  const sources = proposal.sources;
  if (sources.length === 0) return <>{who} proposed it.</>;
  return (
    <>
      {who} picked it up from{' '}
      {sources.map((source, index) => (
        <span key={source.ref}>
          {index > 0 ? (index === sources.length - 1 ? ' and ' : ', ') : null}
          <Source source={source} />
        </span>
      ))}
      .
    </>
  );
}

function Change({
  proposal,
  busy,
  onDecide,
}: {
  proposal: MemoryProposal;
  busy: boolean;
  onDecide: (keep: boolean) => void;
}) {
  return (
    <article
      className="niuu:flex niuu:flex-col niuu:gap-5 niuu:rounded-2xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-6"
      aria-label={proposal.title}
    >
      <h1 className="niuu:m-0 niuu:text-lg niuu:font-semibold niuu:text-text-primary">
        {proposal.title}
      </h1>
      <Part label="It says now">
        <p className={`niuu:m-0 niuu:whitespace-pre-line ${QUIET}`}>
          {proposal.now ?? 'Nothing yet.'}
        </p>
      </Part>
      <Part label="It would say">
        <p className="niuu:m-0 niuu:whitespace-pre-line niuu:text-base niuu:text-text-primary">
          {proposal.would}
        </p>
      </Part>
      <Part label="Why">
        <p className={`niuu:m-0 ${QUIET}`}>
          {proposal.why ? `${proposal.why} ` : null}
          <Origin proposal={proposal} />
        </p>
      </Part>
      <div className="niuu:flex niuu:flex-wrap niuu:gap-2.5 niuu:pt-1">
        <button type="button" className={PRIMARY} disabled={busy} onClick={() => onDecide(true)}>
          Keep
        </button>
        <button type="button" className={SECONDARY} disabled={busy} onClick={() => onDecide(false)}>
          Discard
        </button>
      </div>
    </article>
  );
}

/** One waiting change at a time: what memory says now, what it would say, and why. */
export function ReviewPage() {
  const search = useSearch({ strict: false }) as { layer?: string };
  const review = useMemoryReview(search.layer || undefined);
  const decide = useDecideProposal();
  // How many this visit decided, so the count reads "2 of 2" after the first.
  const [decided, setDecided] = useState(0);

  const proposals = review.data?.proposals ?? [];
  const current = proposals[0];
  const total = decided + proposals.length;
  const more = (review.data?.more.length ?? 0) > 0;

  return (
    <div className={PAGE} data-testid="memory-review-page">
      <div className={COLUMN}>
        {review.isLoading ? <p className={QUIET}>Loading…</p> : null}
        {review.isError ? (
          <p className={ERROR} role="alert">
            {review.error instanceof Error ? review.error.message : String(review.error)}
          </p>
        ) : null}
        {review.data && !current ? (
          <p className={QUIET}>Nothing waits for you. Changes agents propose will show up here.</p>
        ) : null}
        {current ? (
          <>
            <div className="niuu:flex niuu:flex-wrap niuu:items-baseline niuu:justify-between niuu:gap-3">
              <div className={QUIET}>Changes to what {current.layerName || 'memory'} knows</div>
              <div className="niuu:text-sm niuu:text-text-muted">
                {decided + 1} of {total}
                {more ? ' or more' : ''}
              </div>
            </div>
            <Change
              key={current.id}
              proposal={current}
              busy={decide.isPending}
              onDecide={(keep) =>
                decide.mutate(
                  { proposal: current, keep },
                  { onSuccess: () => setDecided((count) => count + 1) },
                )
              }
            />
            {decide.isError ? (
              <p className={ERROR} role="alert">
                {decide.error instanceof Error ? decide.error.message : String(decide.error)}
              </p>
            ) : null}
          </>
        ) : null}
      </div>
    </div>
  );
}
