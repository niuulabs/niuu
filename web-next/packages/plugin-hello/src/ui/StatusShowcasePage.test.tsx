import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StatusShowcasePage } from './StatusShowcasePage';

describe('StatusShowcasePage', () => {
  it('renders the page heading', () => {
    render(<StatusShowcasePage />);
    expect(screen.getByText(/status composites/i)).toBeInTheDocument();
  });

  it('renders status badge grid with all statuses', () => {
    render(<StatusShowcasePage />);
    const grid = screen.getByTestId('status-badge-grid');
    expect(grid.querySelectorAll('[role="status"]').length).toBeGreaterThan(0);
  });

  it('renders confidence bar for each level', () => {
    render(<StatusShowcasePage />);
    const grid = screen.getByTestId('confidence-bar-grid');
    expect(grid.querySelectorAll('[role="meter"]').length).toBe(3);
  });

  it('renders confidence badge for multiple values including null', () => {
    render(<StatusShowcasePage />);
    const grid = screen.getByTestId('confidence-badge-grid');
    expect(grid).toBeInTheDocument();
  });

  it('renders pipe grids', () => {
    render(<StatusShowcasePage />);
    const grid = screen.getByTestId('pipe-grid');
    expect(grid.querySelectorAll('[role="list"]').length).toBeGreaterThan(0);
  });
});
