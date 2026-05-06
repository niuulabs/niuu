import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StatusBadge, type BadgeStatus } from './StatusBadge';

describe('StatusBadge', () => {
  it('renders the status label', () => {
    render(<StatusBadge status="running" />);
    expect(screen.getByRole('status')).toHaveTextContent('running');
  });

  it('has aria-label matching the status', () => {
    render(<StatusBadge status="failed" />);
    expect(screen.getByRole('status')).toHaveAttribute('aria-label', 'failed');
  });

  it.each<[BadgeStatus, string]>([
    ['running', 'niuu-status-badge--run'],
    ['active', 'niuu-status-badge--run'],
    ['complete', 'niuu-status-badge--ok'],
    ['merged', 'niuu-status-badge--ok'],
    ['review', 'niuu-status-badge--warn'],
    ['queued', 'niuu-status-badge--warn'],
    ['escalated', 'niuu-status-badge--warn'],
    ['blocked', 'niuu-status-badge--warn'],
    ['failed', 'niuu-status-badge--crit'],
    ['pending', 'niuu-status-badge--mute'],
    ['archived', 'niuu-status-badge--mute'],
    ['gated', 'niuu-status-badge--gate'],
  ])('maps status %s to tone class %s', (status, toneClass) => {
    render(<StatusBadge status={status} />);
    expect(screen.getByRole('status')).toHaveClass(toneClass);
  });

  it('adds pulse class when pulse=true', () => {
    render(<StatusBadge status="running" pulse />);
    expect(screen.getByRole('status')).toHaveClass('niuu-status-badge--pulse');
  });

  it('does not add pulse class by default', () => {
    render(<StatusBadge status="running" />);
    expect(screen.getByRole('status')).not.toHaveClass('niuu-status-badge--pulse');
  });

  it('forwards className', () => {
    render(<StatusBadge status="queued" className="extra" />);
    expect(screen.getByRole('status')).toHaveClass('extra');
  });

  it('renders a dot element', () => {
    const { container } = render(<StatusBadge status="running" />);
    expect(container.querySelector('.niuu-status-badge__dot')).toBeTruthy();
  });
});
