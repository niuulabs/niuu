import { afterEach, describe, expect, it } from 'vitest';
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
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('reader')).toHaveTextContent('expanded/always/false');
    fireEvent.click(toggle);
    expect(localStorage.getItem('niuu.compactUx.conversationView')).toBe('compact');
    expect(screen.getByTestId('reader')).toHaveTextContent('compact/always/false');
    expect(toggle).toHaveAttribute('title', 'Expanded view');
    fireEvent.click(toggle);
    expect(screen.getByTestId('reader')).toHaveTextContent('expanded/always/false');
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
    expect(screen.getByTestId('reader')).toHaveTextContent('expanded/hover/true');
    expect(localStorage.getItem('niuu.compactUx.showMessageActions')).toBe('false');
    expect(localStorage.getItem('niuu.compactUx.copyMode')).toBe('hover');
  });
});
