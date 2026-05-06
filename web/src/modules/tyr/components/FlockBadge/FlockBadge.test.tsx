import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { FlockBadge } from './FlockBadge';

describe('FlockBadge', () => {
  it('renders flock label without count', () => {
    render(<FlockBadge />);
    expect(screen.getByText('⬡ flock')).toBeInTheDocument();
  });

  it('renders participant count when provided', () => {
    render(<FlockBadge participantCount={3} />);
    expect(screen.getByText('⬡ flock ×3')).toBeInTheDocument();
  });

  it('accepts custom className', () => {
    const { container } = render(<FlockBadge className="extra" />);
    expect(container.firstChild).toHaveClass('extra');
  });
});
