import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ActiveCursor, cursorStateFromStatus } from './ActiveCursor';
import type { SessionStatus } from '../domain/session';

describe('cursorStateFromStatus', () => {
  it('returns active for running', () => {
    expect(cursorStateFromStatus('running')).toBe('active');
  });

  it('returns done for stopped', () => {
    expect(cursorStateFromStatus('stopped')).toBe('done');
  });

  it('returns done for failed', () => {
    expect(cursorStateFromStatus('failed')).toBe('done');
  });

  it('returns idle for idle', () => {
    expect(cursorStateFromStatus('idle')).toBe('idle');
  });
});

describe('ActiveCursor', () => {
  const statuses: SessionStatus[] = ['idle', 'stopped', 'failed'];

  statuses.forEach((status) => {
    it(`renders nothing for status=${status}`, () => {
      const { container } = render(<ActiveCursor status={status} />);
      expect(container.firstChild).toBeNull();
    });
  });

  it('renders a pulsing indicator for running sessions', () => {
    render(<ActiveCursor status="running" />);
    const indicator = screen.getByRole('status', { name: /session in progress/i });
    expect(indicator).toBeInTheDocument();
    expect(indicator).toHaveAttribute('data-cursor-state', 'active');
  });

  it('has aria-live polite for screen readers', () => {
    render(<ActiveCursor status="running" />);
    const indicator = screen.getByRole('status');
    expect(indicator).toHaveAttribute('aria-live', 'polite');
  });

  it('accepts a custom className', () => {
    render(<ActiveCursor status="running" className="my-class" />);
    const indicator = screen.getByRole('status');
    expect(indicator).toHaveClass('my-class');
  });
});
