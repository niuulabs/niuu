import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ModelChip } from './ModelChip';

describe('ModelChip', () => {
  it('renders dash for null model', () => {
    render(<ModelChip model={null} />);
    expect(screen.getByTestId('model-chip')).toHaveTextContent('—');
  });

  it('renders alias from string', () => {
    render(<ModelChip model="sonnet-primary" />);
    expect(screen.getByText('sonnet-primary')).toBeInTheDocument();
  });

  it('renders alias from object', () => {
    render(<ModelChip model={{ alias: 'opus-reasoning', tier: 'reasoning' }} />);
    expect(screen.getByText('opus-reasoning')).toBeInTheDocument();
  });

  it('renders tier indicator dot', () => {
    const { container } = render(<ModelChip model={{ alias: 'test', tier: 'frontier' }} />);
    expect(container.querySelector('[aria-hidden]')).toBeInTheDocument();
  });
});
