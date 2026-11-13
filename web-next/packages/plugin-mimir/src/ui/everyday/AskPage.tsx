import { useId, useState, type FormEvent } from 'react';
import { useSearch } from '@tanstack/react-router';
import { useAskMemory } from '../../application/useMemoryGateway';
import { sourcePlace, type MemoryAnswer } from '../../domain/memoryGateway';
import { COLUMN, ERROR, LINK, PAGE, QUIET, UNSURE } from './styles';

function Answer({ answer }: { answer: MemoryAnswer }) {
  const sources = answer.sources;
  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-7" data-testid="memory-answer">
      <div className="niuu:flex niuu:flex-col niuu:gap-3">
        <p className="niuu:m-0 niuu:text-lg niuu:leading-relaxed niuu:text-text-primary">
          {answer.answer || 'Memory does not know this yet.'}
        </p>
        {answer.gaps.map((gap) => (
          <p key={gap} className={UNSURE}>
            {gap}
          </p>
        ))}
      </div>
      {sources.length > 0 ? (
        <details className="niuu:border-t niuu:border-border-subtle niuu:pt-3">
          <summary className={`niuu:cursor-pointer ${QUIET}`}>
            {sources.length === 1 ? '1 source' : `${sources.length} sources`}
          </summary>
          <ul className="niuu:mt-3 niuu:flex niuu:flex-col niuu:gap-1.5 niuu:pl-5 niuu:text-sm niuu:text-text-secondary">
            {sources.map((source) => (
              <li key={`${source.layer}/${source.ref}`}>
                {source.url ? (
                  <a className={LINK} href={source.url} target="_blank" rel="noreferrer">
                    {source.title}
                  </a>
                ) : (
                  <span className="niuu:text-text-primary">{source.title}</span>
                )}
                , {sourcePlace(source)}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

/** Ask what the team knows: one question, a short cited answer, and what is not settled. */
export function AskPage() {
  const search = useSearch({ strict: false }) as { project?: string; q?: string };
  const id = useId();
  const [question, setQuestion] = useState(search.q ?? '');
  const ask = useAskMemory(search.project || undefined);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (question.trim() && !ask.isPending) ask.mutate(question.trim());
  }

  return (
    <div className={PAGE} data-testid="memory-ask-page">
      <form className={COLUMN} onSubmit={submit} role="search">
        <label htmlFor={id} className={QUIET}>
          Ask what the team knows
        </label>
        <input
          id={id}
          type="text"
          value={question}
          autoFocus
          onChange={(event) => setQuestion(event.target.value)}
          className="niuu:-mt-4 niuu:min-h-14 niuu:w-full niuu:rounded-xl niuu:border niuu:border-border niuu:bg-bg-secondary niuu:px-4 niuu:text-lg niuu:text-text-primary"
        />
        {ask.isPending ? <p className={QUIET}>Looking…</p> : null}
        {ask.isError ? (
          <p className={ERROR} role="alert">
            {ask.error instanceof Error ? ask.error.message : String(ask.error)}
          </p>
        ) : null}
        {ask.data && !ask.isPending ? <Answer answer={ask.data} /> : null}
      </form>
    </div>
  );
}
