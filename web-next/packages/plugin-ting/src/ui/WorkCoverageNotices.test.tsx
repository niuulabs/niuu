import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import type { SourceCoverage } from '../domain/work';
import { WorkCoverageNotices } from './WorkCoverageNotices';

const entry = (status: string, message?: string) =>
  ({
    source: 'tracker',
    status,
    ...(message ? { error: { message } } : {}),
  }) as unknown as SourceCoverage;

describe('WorkCoverageNotices', () => {
  it('stays out of the way when every source answered', () => {
    const { container } = render(<WorkCoverageNotices coverage={[entry('complete')]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('says which source fell short and offers a retry', () => {
    const onRetry = vi.fn();
    render(
      <WorkCoverageNotices coverage={[entry('failed', 'Linear is down')]} onRetry={onRetry} />,
    );
    expect(screen.getByText('Linear is down')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(onRetry).toHaveBeenCalled();
  });

  it('describes partial results without a message', () => {
    render(<WorkCoverageNotices coverage={[entry('partial')]} />);
    expect(screen.getByText('returned partial results')).toBeInTheDocument();
  });
});
