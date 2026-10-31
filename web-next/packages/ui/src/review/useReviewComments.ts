import { useCallback, useMemo, useRef, useState } from 'react';
import type { ReviewComment, ReviewCommentDraft, ReviewControls } from './reviewModel';

export interface ReviewDraft extends ReviewControls {
  clear(): void;
}

/** A review is a draft until sent, so it lives with the page rather than the session. */
export function useReviewComments(): ReviewDraft {
  const [comments, setComments] = useState<readonly ReviewComment[]>([]);
  const nextId = useRef(0);

  const add = useCallback((draft: ReviewCommentDraft) => {
    nextId.current += 1;
    const id = `review-comment-${nextId.current}`;
    setComments((current) => [...current, { ...draft, id }]);
  }, []);
  const remove = useCallback((id: string) => {
    setComments((current) => current.filter((comment) => comment.id !== id));
  }, []);
  const clear = useCallback(() => setComments([]), []);

  return useMemo(() => ({ comments, add, remove, clear }), [comments, add, remove, clear]);
}
