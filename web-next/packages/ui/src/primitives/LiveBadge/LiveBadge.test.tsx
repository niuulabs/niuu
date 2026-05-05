import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { LiveBadge } from './LiveBadge';

describe('LiveBadge', () => {
  it('renders the default LIVE label', () => {
    render(<LiveBadge />);
    expect(screen.getByRole('status', { name: 'LIVE' })).toBeInTheDocument();
  });

  it('accepts a custom label', () => {
    render(<LiveBadge label="STREAMING" />);
    expect(screen.getByRole('status', { name: 'STREAMING' })).toHaveTextContent('STREAMING');
  });
});
