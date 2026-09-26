/**
 * AskAnswerCard — the bottom answer card for Ask mode: the ask bar (still
 * live for re-asking) and, below it, the verbatim Key Facts the answering
 * pages carry, each numbered to match the scene's answer badges (grouped by
 * the page they came from, ranked 1..N), with proof and a page title that
 * focuses that page. A question with no matching page shows "Memory has
 * nothing on <q>." instead — the scene still draws it as an unanswered
 * question (`SceneQuestion` with `nearNodeId: null`), this card just says so.
 */
import { LoadingState } from '@niuulabs/ui';
import { AskBox } from './AskBox';
import { ProofPill } from './ProofPill';
import type { QuotedFact } from '../../domain/quoteFacts';

export interface AskAnswerCardProps {
  question: string;
  quoted: QuotedFact[];
  rankByPath: ReadonlyMap<string, number>;
  nodeIdByPath: ReadonlyMap<string, string>;
  resultCount: number;
  isLoading: boolean;
  isError: boolean;
  onAsk: (question: string) => void;
  onFocusPage: (nodeId: string) => void;
}

export function AskAnswerCard({
  question,
  quoted,
  rankByPath,
  nodeIdByPath,
  resultCount,
  isLoading,
  isError,
  onAsk,
  onFocusPage,
}: AskAnswerCardProps) {
  const nothingFound = !isLoading && !isError && resultCount === 0;

  return (
    <div
      className="niuu:w-full niuu:max-w-2xl niuu:flex niuu:flex-col niuu:gap-2"
      data-testid="memory-ask-answer-card"
    >
      <AskBox value={question} placeholder="Ask what Niuu knows…" onAsk={onAsk} />

      <div className="niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg niuu:p-3 niuu:max-h-64 niuu:overflow-y-auto">
        {isLoading && <LoadingState label="searching memory…" />}
        {isError && (
          <p className="niuu:text-xs niuu:text-critical niuu:m-0" role="alert">
            Search failed.
          </p>
        )}
        {nothingFound && (
          <p className="niuu:text-sm niuu:text-text-secondary niuu:m-0">
            Memory has nothing on &quot;{question}&quot;.
          </p>
        )}
        {!isLoading && !isError && quoted.length > 0 && (
          <ul className="niuu:flex niuu:flex-col niuu:gap-2 niuu:m-0 niuu:p-0 niuu:list-none">
            {quoted.map((entry) => {
              const n = rankByPath.get(entry.page.path);
              const nodeId = nodeIdByPath.get(entry.page.path);
              return (
                <li
                  key={`${entry.page.path}-${entry.position}`}
                  className="niuu:flex niuu:items-start niuu:gap-2 niuu:text-sm"
                >
                  {n !== undefined && (
                    <span className="niuu:font-mono niuu:text-xs niuu:text-brand-300 niuu:shrink-0">
                      [{n}]
                    </span>
                  )}
                  <div className="niuu:flex-1 niuu:flex niuu:flex-col niuu:gap-1">
                    <span className="niuu:text-text-primary">{entry.fact}</span>
                    <div className="niuu:flex niuu:items-center niuu:gap-2">
                      {nodeId ? (
                        <button
                          type="button"
                          onClick={() => onFocusPage(nodeId)}
                          className="niuu:text-xs niuu:text-brand-300 niuu:hover:underline"
                        >
                          {entry.page.title} →
                        </button>
                      ) : (
                        <span className="niuu:text-xs niuu:text-text-muted">
                          {entry.page.title}
                        </span>
                      )}
                      {entry.evidence && <ProofPill evidence={entry.evidence} />}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        {!isLoading && !isError && !nothingFound && quoted.length === 0 && (
          <p className="niuu:text-xs niuu:text-text-faint niuu:m-0">
            The answering pages carry no written facts yet.
          </p>
        )}
      </div>
    </div>
  );
}
