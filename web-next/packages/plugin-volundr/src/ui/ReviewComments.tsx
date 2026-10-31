import { useState } from 'react';
import { MessageSquarePlus, X } from 'lucide-react';
import {
  commentsOnLine,
  type ReviewComment,
  type ReviewControls,
  type ReviewSide,
} from '../domain/reviewComments';
import './ReviewComments.css';

export function ReviewAddButton({
  line,
  side = 'new',
  onClick,
}: {
  line: number;
  side?: ReviewSide;
  onClick: () => void;
}) {
  const label = side === 'old' ? `Comment on removed line ${line}` : `Comment on line ${line}`;
  return (
    <button
      type="button"
      className="niuu-review-add"
      aria-label={label}
      title={label}
      onClick={onClick}
    >
      <MessageSquarePlus aria-hidden="true" />
    </button>
  );
}

function ReviewCommentComposer({
  line,
  onSubmit,
  onCancel,
}: {
  line: number;
  onSubmit: (body: string) => void;
  onCancel: () => void;
}) {
  const [body, setBody] = useState('');
  const submit = () => {
    if (body.trim()) onSubmit(body);
  };
  return (
    <div className="niuu-review-composer">
      <textarea
        aria-label={`Comment for line ${line}`}
        value={body}
        autoFocus
        rows={3}
        placeholder="What should change here?"
        onChange={(event) => setBody(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') onCancel();
          if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) submit();
        }}
      />
      <div className="niuu-review-composer__actions">
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
        <button type="button" disabled={!body.trim()} onClick={submit}>
          Add comment
        </button>
      </div>
    </div>
  );
}

function ReviewCommentCard({
  comment,
  onRemove,
}: {
  comment: ReviewComment;
  onRemove: (id: string) => void;
}) {
  return (
    <div className="niuu-review-comment" data-testid="review-comment">
      <p>{comment.body}</p>
      <button
        type="button"
        aria-label={`Remove comment on line ${comment.line}`}
        title="Remove comment"
        onClick={() => onRemove(comment.id)}
      >
        <X aria-hidden="true" />
      </button>
    </div>
  );
}

/** The comments under one line, plus its composer while that line is being commented on. */
export function ReviewLineThread({
  review,
  filePath,
  side,
  line,
  snippet,
  composing,
  onCloseComposer,
}: {
  review: ReviewControls;
  filePath: string;
  side: ReviewSide;
  line: number;
  snippet: string;
  composing: boolean;
  onCloseComposer: () => void;
}) {
  const comments = commentsOnLine(review.comments, filePath, side, line);
  if (comments.length === 0 && !composing) return null;
  return (
    <div className="niuu-review-thread">
      {comments.map((comment) => (
        <ReviewCommentCard key={comment.id} comment={comment} onRemove={review.remove} />
      ))}
      {composing ? (
        <ReviewCommentComposer
          line={line}
          onCancel={onCloseComposer}
          onSubmit={(body) => {
            review.add({ filePath, side, line, snippet, body });
            onCloseComposer();
          }}
        />
      ) : null}
    </div>
  );
}

/** Numbered source lines with the same comment controls a diff has. */
export function ReviewableText({
  review,
  filePath,
  content,
}: {
  review: ReviewControls;
  filePath: string;
  content: string;
}) {
  const [composingLine, setComposingLine] = useState<number | null>(null);
  return (
    <div className="niuu-review-text" data-testid="reviewable-text">
      {content.split('\n').map((text, index) => {
        const line = index + 1;
        return (
          <div key={line}>
            <div className="niuu-review-line">
              <ReviewAddButton line={line} onClick={() => setComposingLine(line)} />
              <span className="niuu-review-text__number">{line}</span>
              <span className="niuu-review-text__content">{text || ' '}</span>
            </div>
            <ReviewLineThread
              review={review}
              filePath={filePath}
              side="new"
              line={line}
              snippet={text}
              composing={composingLine === line}
              onCloseComposer={() => setComposingLine(null)}
            />
          </div>
        );
      })}
    </div>
  );
}

export function ReviewBar({
  count,
  canSend,
  onSend,
  onDiscard,
}: {
  count: number;
  canSend: boolean;
  onSend: () => void;
  onDiscard: () => void;
}) {
  return (
    <div className="niuu-review-bar" role="region" aria-label="Review draft">
      <span>
        {count} review comment{count === 1 ? '' : 's'} not sent
        {canSend ? '' : ' · connect the session to send'}
      </span>
      <button type="button" onClick={onDiscard}>
        Discard
      </button>
      <button type="button" disabled={!canSend} onClick={onSend}>
        Send to session
      </button>
    </div>
  );
}
