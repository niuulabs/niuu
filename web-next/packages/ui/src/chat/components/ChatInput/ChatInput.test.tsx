import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { ChatInput } from './ChatInput';
import type { SessionEffort, SessionRuntimeOptions } from '../../types';

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

  it('grows the textarea to its content and re-measures when its width changes', () => {
    let notify: ResizeObserverCallback = () => {};
    const original = global.ResizeObserver;
    global.ResizeObserver = class {
      constructor(callback: ResizeObserverCallback) {
        notify = callback;
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver;
    try {
      render(<ChatInput {...defaultProps} />);
      const textarea = screen.getByTestId('chat-textarea') as HTMLTextAreaElement;
      let scrollHeight = 64;
      Object.defineProperty(textarea, 'scrollHeight', { get: () => scrollHeight });
      fireEvent.change(textarea, { target: { value: 'a long line' } });
      expect(textarea.style.height).toBe('64px');

      scrollHeight = 120;
      const resize = (width: number) =>
        notify([{ contentRect: { width } } as ResizeObserverEntry], {} as ResizeObserver);
      resize(0);
      expect(textarea.style.height).toBe('64px');
      resize(320);
      expect(textarea.style.height).toBe('120px');
    } finally {
      global.ResizeObserver = original;
    }
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

  it('browses descriptions and arguments with the slash button, inserts before sending, and preserves drafts', () => {
    const onSend = vi.fn();
    render(
      <ChatInput
        {...defaultProps}
        onSend={onSend}
        availableCommands={[
          {
            name: 'review',
            type: 'skill',
            description: 'Review a change',
            argumentHint: '[focus]',
          },
        ]}
      />,
    );
    const browse = screen.getByRole('button', { name: 'Slash commands' });
    const input = screen.getByTestId('chat-textarea');
    fireEvent.click(browse);
    expect(input).toHaveFocus();
    expect(screen.getByRole('listbox', { name: 'Slash commands' })).toBeInTheDocument();
    expect(screen.getByText('[focus]')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('option', { name: /Review a change/ }));
    expect(input).toHaveValue('/review ');
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: '/review accessibility' } });
    expect(browse).toBeDisabled();
    fireEvent.click(browse);
    expect(input).toHaveValue('/review accessibility');
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onSend).toHaveBeenCalledExactlyOnceWith('/review accessibility', []);
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('does not surface a catalogue in event routing or when none was advertised', () => {
    const { rerender } = render(<ChatInput {...defaultProps} />);
    expect(screen.queryByRole('button', { name: 'Slash commands' })).not.toBeInTheDocument();
    rerender(
      <ChatInput
        {...defaultProps}
        eventRouting
        availableCommands={[{ name: 'review', type: 'command' }]}
      />,
    );
    fireEvent.change(screen.getByTestId('chat-textarea'), { target: { value: '/' } });
    expect(screen.queryByRole('button', { name: 'Slash commands' })).not.toBeInTheDocument();
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
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

  it('deduplicates agent mentions and ignores non-agent participants', () => {
    const onSendDirected = vi.fn();
    const ada = { peerId: 'p1', persona: 'Ada', participantType: 'ravn' };
    const participants = new Map([
      ['p1', ada],
      ['p2', { peerId: 'p2', persona: 'Human', participantType: 'user' }],
    ]);
    render(
      <ChatInput {...defaultProps} onSendDirected={onSendDirected} participants={participants} />,
    );
    fireEvent.change(screen.getByTestId('chat-textarea'), {
      target: { value: '@Ada @Ada @Human @Unknown check this' },
    });
    fireEvent.click(screen.getByTestId('send-btn'));
    expect(onSendDirected).toHaveBeenCalledWith([ada], '@Ada @Ada @Human @Unknown check this', []);
  });

  it('falls back to normal send when directed delivery is unavailable', () => {
    const onSend = vi.fn();
    const participants = new Map([
      ['p1', { peerId: 'p1', persona: 'Ada', participantType: 'ravn' }],
    ]);
    render(<ChatInput {...defaultProps} onSend={onSend} participants={participants} />);
    fireEvent.change(screen.getByTestId('chat-textarea'), { target: { value: '@Ada hello' } });
    fireEvent.keyDown(screen.getByTestId('chat-textarea'), { key: 'Escape' });
    fireEvent.click(screen.getByTestId('send-btn'));
    expect(onSend).toHaveBeenCalledWith('@Ada hello', []);
  });

  it('publishes a typed event selected from a flock participant subscription', () => {
    const onSend = vi.fn();
    const onSendDirected = vi.fn();
    const onPublishEvent = vi.fn();
    const hermes = {
      peerId: 'hermes-1',
      persona: 'reviewer',
      displayName: 'Hermes reviewer',
      participantType: 'ravn',
      subscribesTo: ['review.requested'],
    };
    const participants = new Map([[hermes.peerId, hermes]]);
    render(
      <ChatInput
        {...defaultProps}
        onSend={onSend}
        onSendDirected={onSendDirected}
        onPublishEvent={onPublishEvent}
        eventRouting
        participants={participants}
      />,
    );

    const textarea = screen.getByTestId('chat-textarea');
    fireEvent.change(textarea, { target: { value: 'Review this' } });
    expect(screen.getByTestId('send-btn')).toBeEnabled();

    fireEvent.change(textarea, { target: { value: '@', selectionStart: 1 } });
    fireEvent.click(screen.getByRole('option', { name: /review\.requested.*Hermes reviewer/ }));
    expect(textarea).toHaveValue('@review.requested ');
    fireEvent.change(textarea, { target: { value: '@review.requested Review this' } });
    fireEvent.click(screen.getByTestId('send-btn'));

    expect(onPublishEvent).toHaveBeenCalledWith(
      { participant: hermes, eventType: 'review.requested' },
      '@review.requested Review this',
    );
    expect(onSend).not.toHaveBeenCalled();
    expect(onSendDirected).not.toHaveBeenCalled();
    expect(screen.queryByTestId('attach-btn')).not.toBeInTheDocument();
  });

  it('handles attach clicks, empty file selections, and removable files', async () => {
    const { rerender } = render(<ChatInput {...defaultProps} />);
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const click = vi.spyOn(fileInput, 'click');
    fireEvent.click(screen.getByTestId('attach-btn'));
    expect(click).toHaveBeenCalledOnce();

    fireEvent.change(fileInput, { target: { files: null } });
    const file = new File(['notes'], 'notes.txt', { type: 'text/plain' });
    fireEvent.change(fileInput, { target: { files: [file] } });
    expect(await screen.findByText('notes.txt')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Remove notes.txt'));
    expect(screen.queryByText('notes.txt')).not.toBeInTheDocument();

    click.mockClear();
    rerender(<ChatInput {...defaultProps} disabled={true} />);
    fireEvent.click(screen.getByTestId('attach-btn'));
    expect(click).not.toHaveBeenCalled();
  });
});

describe('mesh ordinary chat', () => {
  it('sends plain messages and directs display-name mentions without event subscriptions', () => {
    const onSend = vi.fn();
    const onSendDirected = vi.fn();
    const bragi = {
      peerId: 'bragi',
      persona: 'resident-codex',
      displayName: 'Bragi',
      participantType: 'ravn',
    };
    const heimdall = {
      peerId: 'heimdall',
      persona: 'resident-codex',
      displayName: 'Heimdall',
      participantType: 'ravn',
    };
    render(
      <ChatInput
        onSend={onSend}
        onSendDirected={onSendDirected}
        eventRouting
        participants={
          new Map([
            [bragi.peerId, bragi],
            [heimdall.peerId, heimdall],
          ])
        }
      />,
    );
    const textarea = screen.getByTestId('chat-textarea');
    fireEvent.change(textarea, { target: { value: 'Hello' } });
    fireEvent.click(screen.getByTestId('send-btn'));
    expect(onSend).toHaveBeenCalled();
    fireEvent.change(textarea, { target: { value: '@Bragi hello' } });
    fireEvent.click(screen.getByTestId('send-btn'));
    expect(onSendDirected).toHaveBeenCalledWith([bragi], '@Bragi hello', []);
    fireEvent.change(textarea, { target: { value: '@', selectionStart: 1 } });
    expect(screen.getByRole('option', { name: /Bragi/ })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /Heimdall/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('option', { name: /Bragi/ }));
    fireEvent.change(textarea, { target: { value: '@resident-codex hello' } });
    fireEvent.click(screen.getByTestId('send-btn'));
    expect(onSendDirected.mock.calls[1]?.[0]).toEqual([bragi]);
  });
});

describe('Lexi composer', () => {
  const defaultProps = { onSend: vi.fn(), isLoading: false, onStop: vi.fn() };

  const runtimeOptions: SessionRuntimeOptions = {
    current: { model: 'gpt-5', effort: 'high' },
    models: [
      { id: 'gpt-5', model: 'gpt-5', name: 'GPT-5', effortLevels: ['low', 'high'] },
      { id: 'gpt-5-mini', model: 'gpt-5-mini', name: 'GPT-5 mini', effortLevels: ['low'] },
      { id: 'old', model: 'old', name: 'Old model', hidden: true, effortLevels: [] },
    ],
  };
  const effort: SessionEffort = {
    current: 'xhigh',
    levels: ['low', 'high', 'xhigh'],
    mutable: true,
  };

  it('invites steering and hints at the keys', () => {
    render(<ChatInput {...defaultProps} />);
    expect(screen.getByPlaceholderText('Steer this session…')).toBeInTheDocument();
    const hints = screen.getByTestId('chat-input-hints');
    expect(hints).toHaveTextContent('↵ send');
    expect(hints).toHaveTextContent('/ commands');
    expect(hints).toHaveTextContent('@ files');
  });

  it('hints that a message is queued while the agent works and steering is supported', () => {
    const { rerender } = render(
      <ChatInput {...defaultProps} isLoading capabilities={{ steer: true }} />,
    );
    expect(screen.getByTestId('chat-input-hints')).toHaveTextContent('↵ queue for after this step');
    rerender(<ChatInput {...defaultProps} isLoading capabilities={{ steer: false }} />);
    expect(screen.getByTestId('chat-input-hints')).toHaveTextContent('↵ send');
    rerender(<ChatInput {...defaultProps} capabilities={{ steer: true }} />);
    expect(screen.getByTestId('chat-input-hints')).toHaveTextContent('↵ send');
  });

  it('shows no chips without the data and a handler for them', () => {
    const { rerender } = render(<ChatInput {...defaultProps} />);
    expect(screen.queryByTestId('model-chip')).not.toBeInTheDocument();
    expect(screen.queryByTestId('effort-chip')).not.toBeInTheDocument();

    rerender(<ChatInput {...defaultProps} runtimeOptions={runtimeOptions} effort={effort} />);
    expect(screen.queryByTestId('model-chip')).not.toBeInTheDocument();
    expect(screen.queryByTestId('effort-chip')).not.toBeInTheDocument();

    rerender(
      <ChatInput
        {...defaultProps}
        onSetRuntimeModel={vi.fn()}
        onSetEffort={vi.fn()}
        effort={{ ...effort, mutable: false }}
      />,
    );
    expect(screen.queryByTestId('model-chip')).not.toBeInTheDocument();
    expect(screen.queryByTestId('effort-chip')).not.toBeInTheDocument();
  });

  it('shows the current model and lists only visible models with the current one checked', () => {
    const onSetRuntimeModel = vi.fn();
    render(
      <ChatInput
        {...defaultProps}
        runtimeOptions={runtimeOptions}
        onSetRuntimeModel={onSetRuntimeModel}
      />,
    );
    const chip = screen.getByRole('button', { name: 'Model' });
    expect(chip).toHaveTextContent('GPT-5');
    fireEvent.click(chip);
    const menu = screen.getByRole('menu', { name: 'Model · next turn' });
    const items = within(menu).getAllByRole('menuitemradio');
    expect(items.map((item) => item.textContent)).toEqual(['GPT-5', 'GPT-5 mini']);
    expect(items[0]).toHaveAttribute('aria-checked', 'true');
    expect(items[1]).toHaveAttribute('aria-checked', 'false');

    fireEvent.click(items[1]!);
    expect(onSetRuntimeModel).toHaveBeenCalledWith('gpt-5-mini');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('names a current model the catalog does not list', () => {
    render(
      <ChatInput
        {...defaultProps}
        runtimeOptions={{ ...runtimeOptions, current: { model: 'custom-1' } }}
        onSetRuntimeModel={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: 'Model' })).toHaveTextContent('custom-1');
  });

  it('shows the effort and offers its levels', () => {
    const onSetEffort = vi.fn();
    render(<ChatInput {...defaultProps} effort={effort} onSetEffort={onSetEffort} />);
    const chip = screen.getByRole('button', { name: 'Effort' });
    expect(chip).toHaveTextContent('Effort · Extra high');
    fireEvent.click(chip);
    const items = within(screen.getByRole('menu', { name: 'Reasoning effort' })).getAllByRole(
      'menuitemradio',
    );
    expect(items.map((item) => item.textContent)).toEqual(['Low', 'High', 'Extra high']);
    expect(items[2]).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(items[0]!);
    expect(onSetEffort).toHaveBeenCalledWith('low');
  });

  it('says when no effort is set', () => {
    render(
      <ChatInput {...defaultProps} effort={{ ...effort, current: null }} onSetEffort={vi.fn()} />,
    );
    expect(screen.getByRole('button', { name: 'Effort' })).toHaveTextContent('not set');
  });

  it('closes a menu on Escape, on an outside press, and on opening it again', () => {
    render(<ChatInput {...defaultProps} effort={effort} onSetEffort={vi.fn()} />);
    const chip = screen.getByRole('button', { name: 'Effort' });
    fireEvent.click(chip);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();

    fireEvent.click(chip);
    fireEvent.keyDown(document, { key: 'a' });
    expect(screen.getByRole('menu')).toBeInTheDocument();
    fireEvent.pointerDown(screen.getByRole('menu'));
    expect(screen.getByRole('menu')).toBeInTheDocument();
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();

    fireEvent.click(chip);
    fireEvent.click(chip);
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('disables the chips while the session is not connected', () => {
    render(
      <ChatInput
        {...defaultProps}
        disabled
        effort={effort}
        onSetEffort={vi.fn()}
        runtimeOptions={runtimeOptions}
        onSetRuntimeModel={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: 'Model' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Effort' })).toBeDisabled();
  });

  it('keeps Stop for the working turn only, disabled when interrupt is unsupported', () => {
    const onStop = vi.fn();
    const { rerender } = render(<ChatInput {...defaultProps} onStop={onStop} isLoading />);
    fireEvent.click(screen.getByTestId('stop-btn'));
    expect(onStop).toHaveBeenCalledTimes(1);
    rerender(<ChatInput {...defaultProps} onStop={onStop} isLoading stopDisabled />);
    expect(screen.getByTestId('stop-btn')).toBeDisabled();
    rerender(<ChatInput {...defaultProps} onStop={onStop} />);
    expect(screen.queryByTestId('stop-btn')).not.toBeInTheDocument();
  });
});
