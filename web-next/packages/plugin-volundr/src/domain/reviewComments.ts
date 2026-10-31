export type ReviewSide = 'old' | 'new';

export interface ReviewComment {
  id: string;
  filePath: string;
  line: number;
  side: ReviewSide;
  /** The line as the reviewer saw it, so the agent can find it after the file moves on. */
  snippet: string;
  body: string;
}

export type ReviewCommentDraft = Omit<ReviewComment, 'id'>;

export interface ReviewControls {
  comments: readonly ReviewComment[];
  add(draft: ReviewCommentDraft): void;
  remove(id: string): void;
}

export function commentsOnLine(
  comments: readonly ReviewComment[],
  filePath: string,
  side: ReviewSide,
  line: number,
): ReviewComment[] {
  return comments.filter(
    (comment) => comment.filePath === filePath && comment.side === side && comment.line === line,
  );
}

function formatComment(comment: ReviewComment): string {
  const label = comment.side === 'old' ? `Line ${comment.line} (removed)` : `Line ${comment.line}`;
  const quote = comment.snippet.trim() ? `> ${comment.snippet.trim()}\n` : '';
  return `${label}:\n${quote}${comment.body.trim()}`;
}

/** Comments reach the agent as one ordinary chat message, grouped by file in reading order. */
export function formatReviewMessage(comments: readonly ReviewComment[]): string {
  const byFile = new Map<string, ReviewComment[]>();
  for (const comment of comments) {
    byFile.set(comment.filePath, [...(byFile.get(comment.filePath) ?? []), comment]);
  }
  const files = [...byFile.entries()].map(([filePath, fileComments]) => {
    const ordered = [...fileComments].sort((a, b) => a.line - b.line);
    return [`**${filePath}**`, ...ordered.map(formatComment)].join('\n\n');
  });
  return ['Review comments. Address each one, then summarize what you changed.', ...files].join(
    '\n\n',
  );
}
