import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ConnectionTypeBadge } from './ConnectionTypeBadge';

describe('ConnectionTypeBadge', () => {
  it('renders CLI label', () => {
    render(<ConnectionTypeBadge connectionType="cli" />);
    expect(screen.getByText('CLI')).toBeInTheDocument();
  });

  it('renders IDE label', () => {
    render(<ConnectionTypeBadge connectionType="ide" />);
    expect(screen.getByText('IDE')).toBeInTheDocument();
  });

  it('renders API label', () => {
    render(<ConnectionTypeBadge connectionType="api" />);
    expect(screen.getByText('API')).toBeInTheDocument();
  });

  it('renders with data-testid', () => {
    render(<ConnectionTypeBadge connectionType="cli" />);
    expect(screen.getByTestId('connection-type-badge')).toBeInTheDocument();
  });

  it('applies custom className', () => {
    render(<ConnectionTypeBadge connectionType="api" className="niuu-ml-2" />);
    expect(screen.getByTestId('connection-type-badge').className).toContain('niuu-ml-2');
  });
});
