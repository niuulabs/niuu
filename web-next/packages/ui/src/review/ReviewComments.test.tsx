import { act, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ReviewableText, ReviewBar } from './ReviewComments';
import { useReviewComments } from './useReviewComments';

function ReviewHarness({ content }: { content: string }) {
  const review = useReviewComments();
  return (
    <>
      <ReviewableText review={review} filePath="/workspace/plan.md" content={content} />
      <output data-testid="draft">{JSON.stringify(review.comments)}</output>
    </>
  );
}

function draft() {
  return JSON.parse(screen.getByTestId('draft').textContent ?? '[]') as Array<
    Record<string, unknown>
  >;
}

describe('ReviewableText', () => {
  it('records a comment against the exact line and text', () => {
    render(<ReviewHarness content={'# Plan\nShip it'} />);

    fireEvent.click(screen.getByRole('button', { name: 'Comment on line 2' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Comment for line 2' }), {
      target: { value: 'Say when' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add comment' }));

    expect(draft()).toEqual([
      expect.objectContaining({
        filePath: '/workspace/plan.md',
        line: 2,
        side: 'new',
        snippet: 'Ship it',
        body: 'Say when',
      }),
    ]);
    expect(screen.getByTestId('review-comment')).toHaveTextContent('Say when');
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('adds with cmd+enter, cancels with escape, and refuses an empty comment', () => {
    render(<ReviewHarness content="only line" />);

    fireEvent.click(screen.getByRole('button', { name: 'Comment on line 1' }));
    expect(screen.getByRole('button', { name: 'Add comment' })).toBeDisabled();

    const box = screen.getByRole('textbox');
    fireEvent.keyDown(box, { key: 'Enter', metaKey: true });
    expect(draft()).toEqual([]);

    fireEvent.keyDown(box, { key: 'Escape' });
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Comment on line 1' }));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Rename' } });
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter', ctrlKey: true });
    expect(draft()).toHaveLength(1);
  });

  it('removes a comment', () => {
    render(<ReviewHarness content="only line" />);
    fireEvent.click(screen.getByRole('button', { name: 'Comment on line 1' }));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Rename' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add comment' }));

    fireEvent.click(screen.getByRole('button', { name: 'Remove comment on line 1' }));

    expect(draft()).toEqual([]);
  });
});

describe('ReviewBar', () => {
  it('sends and discards when the session is connected', () => {
    const onSend = vi.fn();
    const onDiscard = vi.fn();
    render(<ReviewBar count={2} canSend onSend={onSend} onDiscard={onDiscard} />);

    expect(screen.getByText('2 review comments not sent')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Send to session' }));
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));

    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onDiscard).toHaveBeenCalledTimes(1);
  });

  it('holds the draft and says why while the session is offline', () => {
    render(<ReviewBar count={1} canSend={false} onSend={vi.fn()} onDiscard={vi.fn()} />);

    expect(
      screen.getByText('1 review comment not sent · connect the session to send'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Send to session' })).toBeDisabled();
  });
});

describe('useReviewComments', () => {
  it('gives each comment its own id and clears the draft', () => {
    const { result } = renderHook(() => useReviewComments());
    const draftComment = { filePath: 'a', line: 1, side: 'new', snippet: '', body: 'x' } as const;

    act(() => {
      result.current.add(draftComment);
      result.current.add(draftComment);
    });
    const ids = result.current.comments.map((comment) => comment.id);
    expect(new Set(ids).size).toBe(2);

    act(() => result.current.clear());
    expect(result.current.comments).toEqual([]);
  });
});
