import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { SlashCommandMenu } from './SlashCommandMenu';

describe('SlashCommandMenu', () => {
  const commands = [
    { name: 'clear', type: 'command' as const },
    { name: 'compact', type: 'command' as const },
    { name: 'summarize', type: 'skill' as const },
  ];

  it('renders commands', () => {
    render(<SlashCommandMenu commands={commands} selectedIndex={0} onSelect={vi.fn()} />);
    expect(screen.getByText('/clear')).toBeInTheDocument();
    expect(screen.getByText('/compact')).toBeInTheDocument();
    expect(screen.getByText('/summarize')).toBeInTheDocument();
  });

  it('marks selected item', () => {
    render(<SlashCommandMenu commands={commands} selectedIndex={1} onSelect={vi.fn()} />);
    const items = screen.getAllByRole('option');
    expect(items[1]).toHaveClass('niuu-chat-slash-item--selected');
  });

  it('calls onSelect with command when clicked', () => {
    const onSelect = vi.fn();
    render(<SlashCommandMenu commands={commands} selectedIndex={0} onSelect={onSelect} />);
    fireEvent.click(screen.getByText('/clear'));
    expect(onSelect).toHaveBeenCalledWith(commands[0]);
  });

  it('shows empty state for empty commands', () => {
    render(<SlashCommandMenu commands={[]} selectedIndex={0} onSelect={vi.fn()} />);
    expect(screen.getByText('No matching commands')).toBeInTheDocument();
  });
});
