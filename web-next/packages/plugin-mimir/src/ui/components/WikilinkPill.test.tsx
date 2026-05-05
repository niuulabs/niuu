import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { WikilinkPill } from './WikilinkPill';

describe('WikilinkPill', () => {
  it('renders a resolved link as a button', () => {
    render(<WikilinkPill slug="arch/overview" broken={false} />);
    const btn = screen.getByRole('button', { name: /arch\/overview/ });
    expect(btn).toBeInTheDocument();
  });

  it('renders a broken link as a non-interactive span', () => {
    render(<WikilinkPill slug="missing/page" broken />);
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.getByText('[[missing/page]]')).toBeInTheDocument();
  });

  it('calls onNavigate with the slug when a resolved link is clicked', () => {
    const spy = vi.fn();
    render(<WikilinkPill slug="arch/overview" broken={false} onNavigate={spy} />);
    fireEvent.click(screen.getByRole('button'));
    expect(spy).toHaveBeenCalledWith('arch/overview');
  });

  it('does not call onNavigate for broken links', () => {
    const spy = vi.fn();
    render(<WikilinkPill slug="ghost" broken onNavigate={spy} />);
    expect(spy).not.toHaveBeenCalled();
  });

  it('broken link has aria-label describing the broken state', () => {
    render(<WikilinkPill slug="ghost" broken />);
    expect(screen.getByLabelText(/broken link: ghost/)).toBeInTheDocument();
  });

  it('resolved link without onNavigate still renders clickable button', () => {
    render(<WikilinkPill slug="arch/overview" />);
    const btn = screen.getByRole('button');
    expect(btn).toBeInTheDocument();
    // no-op click should not throw
    expect(() => fireEvent.click(btn)).not.toThrow();
  });
});
