import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { SessionEmptyChat } from './ChatEmptyStates';

describe('SessionEmptyChat', () => {
  it('renders session name', () => {
    render(<SessionEmptyChat sessionName="Volundr" onSuggestionClick={vi.fn()} />);
    expect(screen.getByText('Volundr')).toBeInTheDocument();
  });

  it('renders subtitle', () => {
    render(<SessionEmptyChat sessionName="Test" onSuggestionClick={vi.fn()} />);
    expect(screen.getByText(/Start working/)).toBeInTheDocument();
  });

  it('renders suggestion buttons', () => {
    render(<SessionEmptyChat sessionName="Test" onSuggestionClick={vi.fn()} />);
    expect(screen.getByText('Review the code and suggest improvements')).toBeInTheDocument();
    expect(screen.getByText('Run the test suite and fix failures')).toBeInTheDocument();
    expect(screen.getByText('Explain the architecture of this module')).toBeInTheDocument();
  });

  it('calls onSuggestionClick with the correct text', () => {
    const onSuggestionClick = vi.fn();
    render(<SessionEmptyChat sessionName="Test" onSuggestionClick={onSuggestionClick} />);
    fireEvent.click(screen.getByText('Review the code and suggest improvements'));
    expect(onSuggestionClick).toHaveBeenCalledWith('Review the code and suggest improvements');
  });

  it('has correct data-testid', () => {
    render(<SessionEmptyChat sessionName="Test" onSuggestionClick={vi.fn()} />);
    expect(screen.getByTestId('session-empty-chat')).toBeInTheDocument();
  });
});
