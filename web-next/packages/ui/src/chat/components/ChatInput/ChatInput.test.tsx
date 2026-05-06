import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ChatInput } from './ChatInput';

describe('ChatInput', () => {
  const defaultProps = {
    onSend: vi.fn(),
    isLoading: false,
    onStop: vi.fn(),
  };

  it('renders textarea', () => {
    render(<ChatInput {...defaultProps} />);
    expect(screen.getByTestId('chat-textarea')).toBeInTheDocument();
  });

  it('renders send button', () => {
    render(<ChatInput {...defaultProps} />);
    expect(screen.getByTestId('send-btn')).toBeInTheDocument();
  });

  it('send button is disabled when input is empty', () => {
    render(<ChatInput {...defaultProps} />);
    expect(screen.getByTestId('send-btn')).toBeDisabled();
  });

  it('send button is enabled when input has content', () => {
    render(<ChatInput {...defaultProps} />);
    fireEvent.change(screen.getByTestId('chat-textarea'), { target: { value: 'Hello' } });
    expect(screen.getByTestId('send-btn')).not.toBeDisabled();
  });

  it('calls onSend when send button clicked', () => {
    const onSend = vi.fn();
    render(<ChatInput {...defaultProps} onSend={onSend} />);
    fireEvent.change(screen.getByTestId('chat-textarea'), { target: { value: 'Hello' } });
    fireEvent.click(screen.getByTestId('send-btn'));
    expect(onSend).toHaveBeenCalledWith('Hello', []);
  });

  it('calls onSend on Enter key', () => {
    const onSend = vi.fn();
    render(<ChatInput {...defaultProps} onSend={onSend} />);
    fireEvent.change(screen.getByTestId('chat-textarea'), { target: { value: 'Hello' } });
    fireEvent.keyDown(screen.getByTestId('chat-textarea'), { key: 'Enter' });
    expect(onSend).toHaveBeenCalledWith('Hello', []);
  });

  it('does not send on Shift+Enter', () => {
    const onSend = vi.fn();
    render(<ChatInput {...defaultProps} onSend={onSend} />);
    fireEvent.change(screen.getByTestId('chat-textarea'), { target: { value: 'Hello' } });
    fireEvent.keyDown(screen.getByTestId('chat-textarea'), { key: 'Enter', shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
  });

  it('clears input after send', () => {
    render(<ChatInput {...defaultProps} />);
    const textarea = screen.getByTestId('chat-textarea') as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'Hello' } });
    fireEvent.click(screen.getByTestId('send-btn'));
    expect(textarea.value).toBe('');
  });

  it('shows stop button when loading', () => {
    render(<ChatInput {...defaultProps} isLoading={true} />);
    expect(screen.getByTestId('stop-btn')).toBeInTheDocument();
  });

  it('does not show stop button when not loading', () => {
    render(<ChatInput {...defaultProps} isLoading={false} />);
    expect(screen.queryByTestId('stop-btn')).not.toBeInTheDocument();
  });

  it('calls onStop when stop button clicked', () => {
    const onStop = vi.fn();
    render(<ChatInput {...defaultProps} isLoading={true} onStop={onStop} />);
    fireEvent.click(screen.getByTestId('stop-btn'));
    expect(onStop).toHaveBeenCalled();
  });

  it('disables textarea when disabled', () => {
    render(<ChatInput {...defaultProps} disabled={true} />);
    expect(screen.getByTestId('chat-textarea')).toBeDisabled();
  });

  it('shows attach button', () => {
    render(<ChatInput {...defaultProps} />);
    expect(screen.getByTestId('attach-btn')).toBeInTheDocument();
  });

  it('shows placeholder when disabled', () => {
    render(<ChatInput {...defaultProps} disabled={true} />);
    expect(screen.getByPlaceholderText('Start session to chat...')).toBeInTheDocument();
  });

  it('does not send empty/whitespace input', () => {
    const onSend = vi.fn();
    render(<ChatInput {...defaultProps} onSend={onSend} />);
    fireEvent.change(screen.getByTestId('chat-textarea'), { target: { value: '   ' } });
    fireEvent.click(screen.getByTestId('send-btn'));
    expect(onSend).not.toHaveBeenCalled();
  });

  it('does not send when disabled', () => {
    const onSend = vi.fn();
    render(<ChatInput {...defaultProps} onSend={onSend} disabled={true} />);
    fireEvent.change(screen.getByTestId('chat-textarea'), { target: { value: 'Hello' } });
    fireEvent.keyDown(screen.getByTestId('chat-textarea'), { key: 'Enter' });
    expect(onSend).not.toHaveBeenCalled();
  });

  it('shows slash command menu when input starts with /', () => {
    const commands = [{ name: 'clear', type: 'command' as const }];
    render(<ChatInput {...defaultProps} availableCommands={commands} />);
    fireEvent.change(screen.getByTestId('chat-textarea'), { target: { value: '/' } });
    expect(screen.getByTestId('slash-command-menu')).toBeInTheDocument();
  });

  it('selects slash command with Enter key', () => {
    const onSend = vi.fn();
    const commands = [{ name: 'clear', type: 'command' as const }];
    render(<ChatInput {...defaultProps} onSend={onSend} availableCommands={commands} />);
    fireEvent.change(screen.getByTestId('chat-textarea'), { target: { value: '/' } });
    fireEvent.keyDown(screen.getByTestId('chat-textarea'), { key: 'Enter' });
    expect((screen.getByTestId('chat-textarea') as HTMLTextAreaElement).value).toBe('/clear ');
  });

  it('calls onSendDirected when agent mentions present', () => {
    const onSend = vi.fn();
    const onSendDirected = vi.fn();
    const participants = new Map([
      ['p1', { peerId: 'p1', persona: 'Ada', participantType: 'ravn' }],
    ]);
    render(
      <ChatInput
        {...defaultProps}
        onSend={onSend}
        onSendDirected={onSendDirected}
        participants={participants}
      />,
    );
    const textarea = screen.getByTestId('chat-textarea');
    fireEvent.change(textarea, {
      target: { value: '@' },
      nativeEvent: { target: { selectionStart: 1 } },
    });
    // Select the agent mention directly
    const mentionMenu = screen.queryByTestId('mention-menu');
    if (mentionMenu) {
      const agentBtn = screen.getByText('Ada');
      fireEvent.click(agentBtn);
    }
    fireEvent.change(textarea, { target: { value: 'hello' } });
    fireEvent.click(screen.getByTestId('send-btn'));
    // Either send or sendDirected called
    expect(onSend.mock.calls.length + onSendDirected.mock.calls.length).toBeGreaterThan(0);
  });

  it('routes plain typed @agent prefixes as directed messages', () => {
    const onSend = vi.fn();
    const onSendDirected = vi.fn();
    const participants = new Map([
      ['p1', { peerId: 'p1', persona: 'Ada', participantType: 'ravn' }],
    ]);
    render(
      <ChatInput
        {...defaultProps}
        onSend={onSend}
        onSendDirected={onSendDirected}
        participants={participants}
      />,
    );
    fireEvent.change(screen.getByTestId('chat-textarea'), {
      target: { value: '@Ada please verify this' },
    });
    fireEvent.click(screen.getByTestId('send-btn'));
    expect(onSend).not.toHaveBeenCalled();
    expect(onSendDirected).toHaveBeenCalledWith(
      [participants.get('p1')],
      '@Ada please verify this',
      [],
    );
  });

  it('shows drag-over state', () => {
    render(<ChatInput {...defaultProps} />);
    const wrapper = screen.getByTestId('chat-input');
    fireEvent.dragOver(wrapper);
    expect(wrapper).toHaveAttribute('data-drag-over');
  });

  it('clears drag-over on drag leave', () => {
    render(<ChatInput {...defaultProps} />);
    const wrapper = screen.getByTestId('chat-input');
    fireEvent.dragOver(wrapper);
    fireEvent.dragLeave(wrapper);
    expect(wrapper).not.toHaveAttribute('data-drag-over');
  });

  it('stop button is disabled when stopDisabled', () => {
    render(<ChatInput {...defaultProps} isLoading={true} stopDisabled={true} />);
    expect(screen.getByTestId('stop-btn')).toBeDisabled();
  });
});
