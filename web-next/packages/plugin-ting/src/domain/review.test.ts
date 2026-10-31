import { describe, expect, it } from 'vitest';
import {
  addQuoteToNote,
  diffLines,
  isPending,
  openingReview,
  questionAboutQuote,
  type ReviewRevision,
  type RunReview,
} from './review';

function revision(status: string): ReviewRevision {
  return {
    number: 1,
    gateId: 'g',
    activationId: 'a',
    requestedAt: '',
    status,
    summary: '',
    decidedBy: '',
    notes: '',
    decidedAt: '',
    fields: {},
  };
}

function review(columnId: string, status: string): RunReview {
  return {
    columnId,
    title: columnId,
    review: 'plan',
    workflowName: 'Flow',
    taskId: 't',
    sessionId: 's',
    panels: [],
    revisions: [revision(status)],
  };
}

describe('openingReview', () => {
  it('opens the review waiting for a person', () => {
    const reviews = [review('plan', 'pending'), review('code', 'approved')];
    expect(openingReview(reviews)?.columnId).toBe('plan');
  });

  it('opens the newest when nothing is waiting', () => {
    const reviews = [review('plan', 'approved'), review('code', 'approved')];
    expect(openingReview(reviews)?.columnId).toBe('code');
  });

  it('has nothing to open before a gate has', () => {
    expect(openingReview([])).toBeNull();
  });
});

describe('isPending', () => {
  it('is only true for a gate still waiting', () => {
    expect(isPending(revision('pending'))).toBe(true);
    expect(isPending(revision('approved'))).toBe(false);
  });
});

describe('quoting', () => {
  it('starts an empty note with the quote', () => {
    expect(addQuoteToNote('', 'Trace every tick')).toBe('> Trace every tick\n');
  });

  it('keeps what the person already wrote', () => {
    expect(addQuoteToNote('Keep it short.  \n', 'one\ntwo')).toBe(
      'Keep it short.\n\n> one\n> two\n',
    );
  });

  it('puts a question under its quote', () => {
    expect(questionAboutQuote(' Trace every tick ', ' Why not sample? ')).toBe(
      '> Trace every tick\n\nWhy not sample?',
    );
  });
});

describe('diffLines', () => {
  it('keeps unchanged lines and marks what moved', () => {
    expect(diffLines('a\nb\nc', 'a\nx\nc')).toEqual([
      { kind: 'same', text: 'a' },
      { kind: 'removed', text: 'b' },
      { kind: 'added', text: 'x' },
      { kind: 'same', text: 'c' },
    ]);
  });

  it('handles lines added at the end', () => {
    expect(diffLines('a', 'a\nb')).toEqual([
      { kind: 'same', text: 'a' },
      { kind: 'added', text: 'b' },
    ]);
  });

  it('handles lines removed at the end', () => {
    expect(diffLines('a\nb', 'a')).toEqual([
      { kind: 'same', text: 'a' },
      { kind: 'removed', text: 'b' },
    ]);
  });
});
