import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { LintBadge } from './LintBadge';

describe('LintBadge', () => {
  it('renders "clean" when all counts are zero', () => {
    render(<LintBadge summary={{ error: 0, warn: 0, info: 0 }} />);
    expect(screen.getByTestId('lint-badge')).toHaveTextContent('clean');
  });

  it('shows error count when errors > 0', () => {
    render(<LintBadge summary={{ error: 2, warn: 0, info: 0 }} />);
    expect(screen.getByTestId('lint-badge-error')).toHaveTextContent('2 errors');
  });

  it('uses singular "error" for count of 1', () => {
    render(<LintBadge summary={{ error: 1, warn: 0, info: 0 }} />);
    expect(screen.getByTestId('lint-badge-error')).toHaveTextContent('1 error');
  });

  it('shows warn count when warnings > 0', () => {
    render(<LintBadge summary={{ error: 0, warn: 3, info: 0 }} />);
    expect(screen.getByTestId('lint-badge-warn')).toHaveTextContent('3 warnings');
  });

  it('uses singular "warning" for count of 1', () => {
    render(<LintBadge summary={{ error: 0, warn: 1, info: 0 }} />);
    expect(screen.getByTestId('lint-badge-warn')).toHaveTextContent('1 warning');
  });

  it('shows info count when info > 0', () => {
    render(<LintBadge summary={{ error: 0, warn: 0, info: 5 }} />);
    expect(screen.getByTestId('lint-badge-info')).toHaveTextContent('5 info');
  });

  it('shows all three counts together', () => {
    render(<LintBadge summary={{ error: 2, warn: 3, info: 1 }} />);
    expect(screen.getByTestId('lint-badge-error')).toBeInTheDocument();
    expect(screen.getByTestId('lint-badge-warn')).toBeInTheDocument();
    expect(screen.getByTestId('lint-badge-info')).toBeInTheDocument();
  });

  it('does not render error badge when error count is 0', () => {
    render(<LintBadge summary={{ error: 0, warn: 2, info: 1 }} />);
    expect(screen.queryByTestId('lint-badge-error')).not.toBeInTheDocument();
  });

  it('applies aria-label with all counts', () => {
    render(<LintBadge summary={{ error: 2, warn: 3, info: 1 }} />);
    expect(screen.getByTestId('lint-badge')).toHaveAttribute(
      'aria-label',
      '2 errors, 3 warnings, 1 info',
    );
  });

  it('applies aria-label "no lint issues" when clean', () => {
    render(<LintBadge summary={{ error: 0, warn: 0, info: 0 }} />);
    expect(screen.getByTestId('lint-badge')).toHaveAttribute('aria-label', 'no lint issues');
  });

  it('applies sm size class', () => {
    render(<LintBadge summary={{ error: 1, warn: 0, info: 0 }} size="sm" />);
    expect(screen.getByTestId('lint-badge')).toHaveClass('lint-badge--sm');
  });

  it('applies extra className', () => {
    render(<LintBadge summary={{ error: 0, warn: 0, info: 0 }} className="extra" />);
    expect(screen.getByTestId('lint-badge')).toHaveClass('extra');
  });
});
