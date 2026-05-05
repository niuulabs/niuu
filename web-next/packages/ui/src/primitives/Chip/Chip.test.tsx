import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Chip } from './Chip';

describe('Chip', () => {
  it('renders children', () => {
    render(<Chip>hello</Chip>);
    expect(screen.getByText('hello')).toBeInTheDocument();
  });

  it('applies tone modifier class', () => {
    render(<Chip tone="brand">x</Chip>);
    expect(screen.getByText('x')).toHaveClass('niuu-chip--brand');
  });
});
