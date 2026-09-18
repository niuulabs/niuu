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
    expect(screen.getByTestId('reader')).toHaveTextContent('expanded/always/true');
    fireEvent.click(toggle);
    expect(localStorage.getItem('niuu.compactUx.conversationView')).toBe('compact');
    expect(screen.getByTestId('reader')).toHaveTextContent('compact/always/true');
    expect(toggle).toHaveAttribute('title', 'Expanded view');
    fireEvent.click(toggle);
    expect(screen.getByTestId('reader')).toHaveTextContent('expanded/always/true');
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
    expect(screen.getByTestId('reader')).toHaveTextContent('expanded/hover/false');
    expect(localStorage.getItem('niuu.compactUx.showMessageActions')).toBe('false');
    expect(localStorage.getItem('niuu.compactUx.copyMode')).toBe('hover');
  });
});
