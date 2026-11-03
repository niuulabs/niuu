import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ReviewControls } from './reviewModel';
import { FileViewer } from './FileViewer';

vi.mock('shiki', () => ({
  codeToHtml: vi.fn().mockResolvedValue('<pre>highlighted</pre>'),
}));

function review(): ReviewControls {
  return { comments: [], add: vi.fn(), remove: vi.fn() };
}

describe('FileViewer review mode', () => {
  it('stays a plain viewer when the session cannot take a review', () => {
    render(<FileViewer path="/workspace/plan.md" content="# Plan" />);

    expect(screen.queryByTestId('file-viewer-review')).not.toBeInTheDocument();
  });

  it('has nothing to review while loading or after a failed read', () => {
    const { rerender } = render(
      <FileViewer path="/workspace/plan.md" content="" isLoading review={review()} />,
    );
    expect(screen.queryByTestId('file-viewer-review')).not.toBeInTheDocument();

    rerender(
      <FileViewer path="/workspace/plan.md" content="# Plan" error="Gone" review={review()} />,
    );
    expect(screen.queryByTestId('file-viewer-review')).not.toBeInTheDocument();
  });

  it('switches to numbered lines that take comments against the full path', () => {
    const controls = review();
    render(<FileViewer path="/workspace/plan.md" content={'# Plan\nShip it'} review={controls} />);

    fireEvent.click(screen.getByTestId('file-viewer-review'));
    expect(screen.getByTestId('reviewable-text')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Comment on line 2' }));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Say when' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add comment' }));

    expect(controls.add).toHaveBeenCalledWith({
      filePath: '/workspace/plan.md',
      side: 'new',
      line: 2,
      snippet: 'Ship it',
      body: 'Say when',
    });
  });

  it('returns to the highlighted view', () => {
    render(<FileViewer path="/workspace/plan.md" content="# Plan" review={review()} />);

    fireEvent.click(screen.getByTestId('file-viewer-review'));
    fireEvent.click(screen.getByTestId('file-viewer-review'));

    expect(screen.queryByTestId('reviewable-text')).not.toBeInTheDocument();
  });
});
