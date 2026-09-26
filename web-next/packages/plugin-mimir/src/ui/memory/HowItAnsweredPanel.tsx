/**
 * HowItAnsweredPanel — left inspector shown in Ask mode (a question is asked
 * and no page is focused): explains the retrieval method, what was searched,
 * how long it took, and offers "Follow up" to ask something else.
 */
import { LoadingState } from '@niuulabs/ui';

export interface HowItAnsweredPanelProps {
  question: string;
  mountName: string | undefined;
  resultCount: number;
  isLoading: boolean;
  isError: boolean;
  elapsedSeconds: number | null;
  onFollowUp: () => void;
}

export function HowItAnsweredPanel({
  question,
  mountName,
  resultCount,
  isLoading,
  isError,
  elapsedSeconds,
  onFollowUp,
}: HowItAnsweredPanelProps) {
  const searchedLabel = mountName ?? 'every mount';

  return (
    <section
      className="niuu:w-80 niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg niuu:p-4 niuu:flex niuu:flex-col niuu:gap-3"
      aria-label="How it answered"
    >
      <div>
        <h2 className="niuu:text-lg niuu:font-semibold niuu:text-text-primary niuu:m-0">
          How it answered
        </h2>
        <p className="niuu:text-xs niuu:text-text-muted niuu:m-0 niuu:mt-1">
          &quot;{question}&quot;
        </p>
      </div>

      {isLoading && <LoadingState label="searching memory…" />}

      {isError && (
        <p className="niuu:text-xs niuu:text-critical niuu:m-0" role="alert">
          Search failed.
        </p>
      )}

      {!isLoading && !isError && (
        <ul className="niuu:flex niuu:flex-col niuu:gap-1.5 niuu:m-0 niuu:p-0 niuu:list-none niuu:text-xs niuu:text-text-secondary">
          <li>
            hybrid search (keyword + meaning) · searched: <strong>{searchedLabel}</strong>
          </li>
          <li>
            <span className="niuu:text-text-primary">{resultCount}</span>{' '}
            {resultCount === 1 ? 'page answers' : 'pages answer'}
          </li>
          {elapsedSeconds !== null && <li>{elapsedSeconds.toFixed(2)}s</li>}
        </ul>
      )}

      <button
        type="button"
        onClick={onFollowUp}
        className="niuu:mt-auto niuu:self-start niuu:px-3 niuu:py-1.5 niuu:text-sm niuu:rounded-sm niuu:border niuu:border-border niuu:text-text-secondary"
      >
        Follow up
      </button>
    </section>
  );
}
