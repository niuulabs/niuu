import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ClusterChip } from './ClusterChip';

describe('ClusterChip', () => {
  it('renders an em dash when no cluster is available', () => {
    render(<ClusterChip cluster={null} />);

    expect(screen.getByTestId('cluster-chip')).toHaveTextContent('—');
  });

  it('renders cluster details with the mapped kind class', () => {
    render(<ClusterChip cluster={{ name: 'tor-1', kind: 'gpu' }} className="extra-class" />);

    expect(screen.getByTestId('cluster-chip')).toHaveClass('extra-class');
    expect(screen.getByText('tor-1')).toBeInTheDocument();
    expect(screen.getByText('gpu')).toHaveClass('niuu:text-state-warn');
  });
});
