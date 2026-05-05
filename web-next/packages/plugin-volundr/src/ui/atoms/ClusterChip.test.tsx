import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ClusterChip } from './ClusterChip';

describe('ClusterChip', () => {
  it('renders dash for null cluster', () => {
    render(<ClusterChip cluster={null} />);
    expect(screen.getByTestId('cluster-chip')).toHaveTextContent('—');
  });

  it('renders cluster name and kind', () => {
    render(<ClusterChip cluster={{ name: 'Valaskjálf', kind: 'primary' }} />);
    expect(screen.getByText('Valaskjálf')).toBeInTheDocument();
    expect(screen.getByText('primary')).toBeInTheDocument();
  });

  it('renders gpu kind', () => {
    render(<ClusterChip cluster={{ name: 'Valhalla', kind: 'gpu' }} />);
    expect(screen.getByText('Valhalla')).toBeInTheDocument();
    expect(screen.getByText('gpu')).toBeInTheDocument();
  });
});
