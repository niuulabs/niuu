import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { ChatDisplayControls } from './ChatDisplayControls';
import { useCompactUxChatPrefs, useConversationView } from '../../compactUxPrefs';

function Reader() {
  const view = useConversationView();
  const prefs = useCompactUxChatPrefs();
  return (
    <output data-testid="reader">
      {view}/{prefs.timestamp}/{String(prefs.showAgentAvatar)}
    </output>
  );
}

describe('ChatDisplayControls', () => {
  afterEach(() => {
    localStorage.clear();
  });

  it('switches every open conversation between expanded and compact turns', () => {
    render(
      <>
        <ChatDisplayControls className="host-toolbar" />
        <Reader />
      </>,
    );
    const toggle = screen.getByTestId('conversation-view-toggle');
    expect(toggle.closest('.niuu-chat-display-menu')).toHaveClass('host-toolbar');
    // Compact first, as Lexi folds a turn's work.
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByTestId('reader')).toHaveTextContent('compact/always/false');
    fireEvent.click(toggle);
    expect(localStorage.getItem('niuu.compactUx.conversationFold')).toBe('expanded');
    expect(screen.getByTestId('reader')).toHaveTextContent('expanded/always/false');
    expect(toggle).toHaveAttribute('title', 'Compact view');
    fireEvent.click(toggle);
    expect(screen.getByTestId('reader')).toHaveTextContent('compact/always/false');
  });

  it('offers the host tool-call visibility in the same menu', () => {
    const onToggle = vi.fn();
    const { rerender } = render(<ChatDisplayControls toolCalls={{ shown: true, onToggle }} />);
    const toggle = screen.getByTestId('tool-calls-toggle');
    expect(toggle.closest('details')).toHaveClass('niuu-chat-preferences');
    expect(toggle).toHaveAccessibleName('Hide tool calls and results');
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(toggle);
    expect(onToggle).toHaveBeenCalled();
    rerender(<ChatDisplayControls toolCalls={{ shown: false, onToggle }} />);
    expect(screen.getByTestId('tool-calls-toggle')).toHaveAccessibleName(
      'Show tool calls and results',
    );
  });

  it('leaves tool calls out when the host has no such switch', () => {
    render(<ChatDisplayControls />);
    expect(screen.queryByTestId('tool-calls-toggle')).not.toBeInTheDocument();
  });

  it('stores the Display preferences where every conversation reads them', () => {
    render(
      <>
        <ChatDisplayControls />
        <Reader />
      </>,
    );
    fireEvent.click(screen.getByLabelText('Agent avatars'));
    fireEvent.change(screen.getByLabelText('Timestamps'), { target: { value: 'hover' } });
    fireEvent.click(screen.getByLabelText('Message actions'));
    fireEvent.change(screen.getByLabelText('Copy button'), { target: { value: 'hover' } });
    fireEvent.change(screen.getByLabelText('Tool calls'), { target: { value: 'cards' } });
    expect(localStorage.getItem('niuu.compactUx.toolRuns')).toBe('cards');
    fireEvent.change(screen.getByLabelText('Tool calls'), { target: { value: 'rows' } });
    expect(localStorage.getItem('niuu.compactUx.toolRuns')).toBe('rows');
    expect(screen.getByTestId('reader')).toHaveTextContent('compact/hover/true');
    expect(localStorage.getItem('niuu.compactUx.showMessageActions')).toBe('false');
    expect(localStorage.getItem('niuu.compactUx.copyMode')).toBe('hover');
  });

  it("offers the host's workflow panel only when the host has one", () => {
    const onToggle = vi.fn();
    const { rerender } = render(<ChatDisplayControls />);
    expect(screen.queryByTestId('workflow-panel-toggle')).toBeNull();

    rerender(<ChatDisplayControls workflowPanel={{ shown: true, onToggle }} />);
    const toggle = screen.getByTestId('workflow-panel-toggle');
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    expect(toggle).toHaveAccessibleName('Hide the workflow');

    fireEvent.click(toggle);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});
