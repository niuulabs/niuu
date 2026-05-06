import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { AdvancedSection } from './AdvancedSection';

describe('AdvancedSection', () => {
  it('renders the section heading', () => {
    render(<AdvancedSection />);
    expect(screen.getByText('Advanced')).toBeInTheDocument();
  });

  it('renders the description', () => {
    render(<AdvancedSection />);
    expect(
      screen.getByText('Danger zone. These actions can disrupt running sagas and raids.'),
    ).toBeInTheDocument();
  });

  it('renders all 3 action rows', () => {
    render(<AdvancedSection />);
    expect(screen.getByText('Flush queue')).toBeInTheDocument();
    expect(screen.getByText('Reset dispatcher')).toBeInTheDocument();
    expect(screen.getByText('Rebuild confidence scores')).toBeInTheDocument();
  });

  it('renders the action buttons', () => {
    render(<AdvancedSection />);
    expect(screen.getByText('Flush')).toBeInTheDocument();
    expect(screen.getByText('Reset')).toBeInTheDocument();
    expect(screen.getByText('Rebuild')).toBeInTheDocument();
  });

  it('shows confirm message on first click of a danger button', () => {
    render(<AdvancedSection />);
    const flushBtn = screen.getByTestId('action-flush-queue');
    fireEvent.click(flushBtn);
    expect(
      screen.getByText('Are you sure you want to flush the dispatch queue? This cannot be undone.'),
    ).toBeInTheDocument();
  });

  it('fires onAction callback on confirm (second click)', () => {
    const onAction = vi.fn();
    render(<AdvancedSection onAction={onAction} />);
    const flushBtn = screen.getByTestId('action-flush-queue');
    fireEvent.click(flushBtn);
    expect(
      screen.getByText('Are you sure you want to flush the dispatch queue? This cannot be undone.'),
    ).toBeInTheDocument();
    fireEvent.click(flushBtn);
    expect(onAction).toHaveBeenCalledWith('Flush queue');
    // Confirmation text clears after action
    expect(
      screen.queryByText(
        'Are you sure you want to flush the dispatch queue? This cannot be undone.',
      ),
    ).not.toBeInTheDocument();
  });

  it('has an accessible section label', () => {
    render(<AdvancedSection />);
    expect(screen.getByRole('region', { name: /advanced settings/i })).toBeInTheDocument();
  });

  it('renders 3 action buttons total', () => {
    render(<AdvancedSection />);
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(3);
  });

  it('shows confirm message with specific text for each action', () => {
    render(<AdvancedSection />);
    const resetBtn = screen.getByTestId('action-reset-dispatcher');
    fireEvent.click(resetBtn);
    expect(screen.getByTestId('confirm-msg-reset-dispatcher')).toHaveTextContent(
      'Are you sure you want to reset the dispatcher?',
    );
  });
});
