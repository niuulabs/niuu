import { useState } from 'react';
import { Check, X } from 'lucide-react';
import { reviewEffectStatement, type ReviewItem } from '../domain';
import { useDecideReview } from '../application/useReviews';
import { statusClasses } from './reviewFormat';

const PANEL =
  'niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary niuu:rounded-md';
const MUTED = 'niuu:text-text-muted';
const BUTTON =
  'niuu:inline-flex niuu:items-center niuu:justify-center niuu:gap-2 niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:text-text-primary niuu:hover:border-brand niuu:disabled:opacity-50';
const SIZE = 'niuu:px-3 niuu:py-2 niuu:text-sm';
const COMPACT_SIZE = 'niuu:px-2.5 niuu:py-1 niuu:text-xs';

export function isOperatorQuestion(item: ReviewItem): boolean {
  return (
    typeof item.evidence.operator_question === 'object' && item.evidence.operator_question !== null
  );
}

/**
 * Decide one review. A resident's question takes an answer and Yes/No; anything
 * else takes Approve/Reject, and a rejection needs a reason for the audit trail.
 * `compact` drops the effect statement for use inside a list row.
 */
export function ReviewDecisionPanel({
  item,
  compact = false,
}: {
  item: ReviewItem;
  compact?: boolean;
}) {
  const decide = useDecideReview();
  const [reason, setReason] = useState('');
  const [validationError, setValidationError] = useState('');
  const question = isOperatorQuestion(item);
  const size = compact ? COMPACT_SIZE : SIZE;

  const submit = (decision: 'approved' | 'rejected') => {
    if (decision === 'rejected' && !reason.trim() && !question) {
      setValidationError('A reason is required to reject.');
      return;
    }
    setValidationError('');
    decide.mutate({
      itemId: item.itemId,
      decision,
      reason:
        decision === 'rejected' && question && !reason.trim() ? 'No, do not proceed.' : reason,
      participantId: 'human:operator',
    });
  };

  if (item.status !== 'pending') {
    return (
      <div data-testid="review-verdict" className={`${PANEL} niuu:p-3 niuu:text-sm`}>
        <span className={statusClasses(item.status)}>{item.status.replace('_', ' ')}</span>
        <span className={`niuu:ml-2 ${MUTED}`}>
          by {item.decidedBy || 'operator'}
          {item.decisionReason ? ` — ${item.decisionReason}` : ''}
        </span>
        {item.applyDetail ? (
          <p className={`niuu:mt-1 niuu:text-xs ${MUTED}`}>{item.applyDetail}</p>
        ) : null}
      </div>
    );
  }

  return (
    <div data-testid="review-decision" className="niuu:flex niuu:flex-col niuu:gap-2">
      {compact ? null : (
        <p className={`niuu:rounded-md niuu:bg-bg-tertiary niuu:p-2 niuu:text-xs ${MUTED}`}>
          {reviewEffectStatement(item)}
        </p>
      )}
      <div className="niuu:flex niuu:items-center niuu:gap-2">
        <input
          aria-label={question ? 'Operator answer' : 'Decision reason'}
          placeholder={
            question
              ? 'Optional details or an exact answer'
              : 'Reason (recorded in the audit trail)'
          }
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          className={`niuu:min-w-0 niuu:flex-1 niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:text-text-primary ${size}`}
        />
        <button
          type="button"
          className={`${BUTTON} ${size} niuu:text-state-ok`}
          disabled={decide.isPending}
          onClick={() => submit('approved')}
        >
          <Check size={compact ? 12 : 14} aria-hidden="true" />
          {question ? 'Yes' : 'Approve'}
        </button>
        <button
          type="button"
          className={`${BUTTON} ${size} niuu:text-critical`}
          disabled={decide.isPending}
          onClick={() => submit('rejected')}
        >
          <X size={compact ? 12 : 14} aria-hidden="true" />
          {question ? 'No' : 'Reject'}
        </button>
      </div>
      {validationError ? (
        <p role="alert" className="niuu:m-0 niuu:text-xs niuu:text-critical">
          {validationError}
        </p>
      ) : null}
      {decide.isError ? (
        <p role="alert" className="niuu:m-0 niuu:text-xs niuu:text-critical">
          {decide.error instanceof Error ? decide.error.message : 'Decision failed'}
        </p>
      ) : null}
    </div>
  );
}
