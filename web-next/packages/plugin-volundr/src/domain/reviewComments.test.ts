import { describe, expect, it } from 'vitest';
import { commentsOnLine, formatReviewMessage, type ReviewComment } from './reviewComments';

function comment(overrides: Partial<ReviewComment>): ReviewComment {
  return {
    id: 'c',
    filePath: 'src/auth.py',
    line: 1,
    side: 'new',
    snippet: 'code',
    body: 'Change this',
    ...overrides,
  };
}

describe('formatReviewMessage', () => {
  it('groups comments by file in line order, quoting each line', () => {
    const message = formatReviewMessage([
      comment({ id: '1', line: 42, snippet: '  raise ValueError', body: 'Use a typed error' }),
      comment({ id: '2', filePath: 'README.md', line: 3, snippet: 'Intro', body: 'Too long' }),
      comment({ id: '3', line: 7, snippet: 'import os', body: 'Unused' }),
    ]);

    expect(message).toBe(
      [
        'Review comments. Address each one, then summarize what you changed.',
        '**src/auth.py**',
        'Line 7:\n> import os\nUnused',
        'Line 42:\n> raise ValueError\nUse a typed error',
        '**README.md**',
        'Line 3:\n> Intro\nToo long',
      ].join('\n\n'),
    );
  });

  it('says when a comment is on a removed line, and skips an empty quote', () => {
    const message = formatReviewMessage([
      comment({ line: 9, side: 'old', snippet: '   ', body: 'Keep this line' }),
    ]);

    expect(message).toContain('Line 9 (removed):\nKeep this line');
  });
});

describe('commentsOnLine', () => {
  it('matches file, side and line, so old and new line 5 stay separate', () => {
    const newLine = comment({ id: 'new', line: 5 });
    const oldLine = comment({ id: 'old', line: 5, side: 'old' });
    const otherFile = comment({ id: 'other', line: 5, filePath: 'other.py' });

    expect(commentsOnLine([newLine, oldLine, otherFile], 'src/auth.py', 'new', 5)).toEqual([
      newLine,
    ]);
  });
});
