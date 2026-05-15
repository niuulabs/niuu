import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MiniBar } from './MiniBar';

describe('MiniBar', () => {
  it('renders a cool utilization bar by default', () => {
    const { container } = render(<MiniBar value={0.4} label="CPU" />);

    expect(screen.getByRole('progressbar', { name: 'CPU utilization' })).toHaveAttribute(
      'aria-valuenow',
      '40',
    );
    expect(container.querySelector('.niuu-bg-brand')).toHaveStyle({ width: '40%' });
  });

  it('renders a warning bar for medium utilization', () => {
    const { container } = render(<MiniBar value={0.7} label="Memory" />);

    expect(container.querySelector('.niuu-bg-state-warn')).toHaveStyle({ width: '70%' });
  });

  it('renders a critical bar for high utilization', () => {
    const { container } = render(<MiniBar value={0.9} label="GPU" />);

    expect(container.querySelector('.niuu-bg-critical')).toHaveStyle({ width: '90%' });
  });
});
