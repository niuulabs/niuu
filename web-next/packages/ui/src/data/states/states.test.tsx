import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { EmptyState } from './EmptyState';
import { LoadingState } from './LoadingState';
import { ErrorState } from './ErrorState';

describe('EmptyState', () => {
  it('renders title', () => {
    render(<EmptyState title="No results" />);
    expect(screen.getByText('No results')).toBeInTheDocument();
  });

  it('renders icon when provided', () => {
    render(<EmptyState title="Empty" icon={<span data-testid="icon">🔍</span>} />);
    expect(screen.getByTestId('icon')).toBeInTheDocument();
  });

  it('renders description', () => {
    render(<EmptyState title="Empty" description="Try adjusting filters" />);
    expect(screen.getByText('Try adjusting filters')).toBeInTheDocument();
  });

  it('renders action slot', () => {
    render(<EmptyState title="Empty" action={<button>Reset</button>} />);
    expect(screen.getByRole('button', { name: 'Reset' })).toBeInTheDocument();
  });

  it('has role=status', () => {
    render(<EmptyState title="Empty" />);
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('applies custom className', () => {
    const { container } = render(<EmptyState title="Empty" className="custom" />);
    expect(container.firstChild).toHaveClass('custom');
  });

  it('omits icon when not provided', () => {
    render(<EmptyState title="Empty" />);
    expect(document.querySelector('.niuu-state__icon')).not.toBeInTheDocument();
  });

  it('omits description when not provided', () => {
    const { container } = render(<EmptyState title="Empty" />);
    const descs = container.querySelectorAll('.niuu-state__desc');
    expect(descs.length).toBe(0);
  });

  it('omits action when not provided', () => {
    const { container } = render(<EmptyState title="Empty" />);
    expect(container.querySelector('.niuu-state__action')).not.toBeInTheDocument();
  });
});

describe('LoadingState', () => {
  it('renders default label', () => {
    render(<LoadingState />);
    expect(screen.getByText('Loading…')).toBeInTheDocument();
  });

  it('renders custom label', () => {
    render(<LoadingState label="Fetching data…" />);
    expect(screen.getByText('Fetching data…')).toBeInTheDocument();
  });

  it('has role=status', () => {
    render(<LoadingState />);
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('has aria-label matching label text', () => {
    render(<LoadingState label="Fetching…" />);
    expect(screen.getByRole('status')).toHaveAttribute('aria-label', 'Fetching…');
  });

  it('applies custom className', () => {
    const { container } = render(<LoadingState className="custom" />);
    expect(container.firstChild).toHaveClass('custom');
  });

  it('renders spinner element', () => {
    const { container } = render(<LoadingState />);
    expect(container.querySelector('.niuu-state__spinner')).toBeInTheDocument();
  });
});

describe('ErrorState', () => {
  it('renders message', () => {
    render(<ErrorState message="Request failed" />);
    expect(screen.getByText('Request failed')).toBeInTheDocument();
  });

  it('renders default title', () => {
    render(<ErrorState message="oops" />);
    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
  });

  it('renders custom title', () => {
    render(<ErrorState title="Connection refused" message="oops" />);
    expect(screen.getByText('Connection refused')).toBeInTheDocument();
  });

  it('renders icon when provided', () => {
    render(<ErrorState message="err" icon={<span data-testid="err-icon">⚠</span>} />);
    expect(screen.getByTestId('err-icon')).toBeInTheDocument();
  });

  it('renders action slot', () => {
    const onRetry = vi.fn();
    render(<ErrorState message="err" action={<button onClick={onRetry}>Retry</button>} />);
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });

  it('action button is clickable', async () => {
    const onRetry = vi.fn();
    render(<ErrorState message="err" action={<button onClick={onRetry}>Retry</button>} />);
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it('has role=alert', () => {
    render(<ErrorState message="err" />);
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('applies custom className', () => {
    const { container } = render(<ErrorState message="err" className="custom" />);
    expect(container.firstChild).toHaveClass('custom');
  });

  it('omits icon when not provided', () => {
    render(<ErrorState message="err" />);
    expect(document.querySelector('.niuu-state__icon')).not.toBeInTheDocument();
  });

  it('omits action when not provided', () => {
    const { container } = render(<ErrorState message="err" />);
    expect(container.querySelector('.niuu-state__action')).not.toBeInTheDocument();
  });
});
