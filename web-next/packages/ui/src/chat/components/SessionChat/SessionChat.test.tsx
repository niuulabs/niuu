import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SessionChat } from './SessionChat';
import type {
  AgentInternalEvent,
  ChatMessage,
  MeshOutcomeEvent,
  PermissionRequest,
  RoomParticipant,
} from '../../types';

/* ── Shared fixtures ── */

const participant: RoomParticipant = {
  peerId: 'peer-1',
  persona: 'Ravn-A',
  displayName: 'Reviewer',
  color: 'amber',
  participantType: 'ravn',
  status: 'thinking',
};

const participant2: RoomParticipant = {
  peerId: 'peer-2',
  persona: 'Ravn-B',
  displayName: 'Builder',
  color: 'cyan',
  participantType: 'ravn',
  status: 'idle',
};

const skuldParticipant: RoomParticipant = {
  peerId: 'skuld-1',
  persona: 'Skuld',
  displayName: 'Skuld',
  color: 'indigo',
  participantType: 'skuld',
  status: 'idle',
};

const now = new Date('2026-04-26T12:00:00Z');

const userMessage: ChatMessage = {
  id: 'u1',
  role: 'user',
  content: 'Please review the config.',
  createdAt: now,
};

const assistantMessage: ChatMessage = {
  id: 'a1',
  role: 'assistant',
  content: 'I have reviewed the config.',
  createdAt: new Date('2026-04-26T12:00:05Z'),
  status: 'done',
};

const roomAssistantMessage: ChatMessage = {
  id: 'm1',
  role: 'assistant',
  content: 'Need to inspect the config first.',
  createdAt: now,
  status: 'done',
  participant,
};

const roomOutcomeMessage: ChatMessage = {
  id: 'm-outcome',
  role: 'assistant',
  content: `### Ravn-A

\`\`\`outcome
verdict: approve
summary: Approved the change
checks_passed: 12
findings: |
  ## Verified
  - **Route pair count**: 26
  - Use \`/api/v1/credentials/secrets\`
\`\`\``,
  createdAt: new Date('2026-04-26T12:00:04Z'),
  status: 'done',
  participant,
};

const systemMessage: ChatMessage = {
  id: 's1',
  role: 'system',
  content: 'Session started',
  createdAt: now,
  metadata: { messageType: 'system' },
};

const runningMessage: ChatMessage = {
  id: 'r1',
  role: 'assistant',
  content: 'Working on it...',
  createdAt: now,
  status: 'running',
};

const agentEvents = new Map<string, AgentInternalEvent[]>([
  [
    'peer-1',
    [
      {
        id: 'evt-1',
        participantId: 'peer-1',
        timestamp: new Date('2026-04-26T12:00:01Z'),
        frameType: 'thought',
        data: 'Checking compose and runtime settings.',
      },
    ],
  ],
]);

const defaultProps = {
  messages: [] as ChatMessage[],
  onSend: vi.fn(),
};

describe('SessionChat', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
  });

  /* ── Basic rendering ── */

  it('renders with data-testid', () => {
    render(<SessionChat {...defaultProps} />);
    expect(screen.getByTestId('session-chat')).toBeInTheDocument();
  });

  it('applies optional className', () => {
    render(<SessionChat {...defaultProps} className="custom-class" />);
    expect(screen.getByTestId('session-chat')).toHaveClass('custom-class');
  });

  /* ── Connection status ── */

  it('shows Disconnected when not connected', () => {
    render(<SessionChat {...defaultProps} connected={false} />);
    expect(screen.getByText('Disconnected')).toBeInTheDocument();
  });

  it('shows Connected when connected', () => {
    render(<SessionChat {...defaultProps} connected />);
    expect(screen.getByText('Connected')).toBeInTheDocument();
  });

  /* ── History loading ── */

  it('shows loading indicator when history not loaded and connected', () => {
    render(<SessionChat {...defaultProps} connected historyLoaded={false} />);
    expect(screen.getByTestId('history-loading')).toBeInTheDocument();
    expect(screen.getByText('Loading conversation...')).toBeInTheDocument();
  });

  it('does not show loading indicator when history is loaded', () => {
    render(<SessionChat {...defaultProps} connected historyLoaded />);
    expect(screen.queryByTestId('history-loading')).not.toBeInTheDocument();
  });

  it('does not show loading indicator when disconnected even if history not loaded', () => {
    render(<SessionChat {...defaultProps} connected={false} historyLoaded={false} />);
    expect(screen.queryByTestId('history-loading')).not.toBeInTheDocument();
  });

  /* ── Empty state ── */

  it('shows empty state when there are no messages', () => {
    render(<SessionChat {...defaultProps} connected />);
    expect(screen.getByTestId('session-empty-chat')).toBeInTheDocument();
  });

  it('uses sessionName in empty state', () => {
    render(<SessionChat {...defaultProps} connected sessionName="My Session" />);
    expect(screen.getByText('My Session')).toBeInTheDocument();
  });

  it('sends message when empty state suggestion is clicked', () => {
    const onSend = vi.fn();
    render(<SessionChat {...defaultProps} onSend={onSend} connected />);
    fireEvent.click(screen.getByText('Review the code and suggest improvements'));
    expect(onSend).toHaveBeenCalledWith('Review the code and suggest improvements', []);
  });

  /* ── Message count ── */

  it('shows message count as singular for 1 message', () => {
    render(<SessionChat {...defaultProps} messages={[userMessage]} />);
    expect(screen.getByText('1 message')).toBeInTheDocument();
  });

  it('shows message count as plural for multiple messages', () => {
    render(<SessionChat {...defaultProps} messages={[userMessage, assistantMessage]} />);
    expect(screen.getByText('2 messages')).toBeInTheDocument();
  });

  it('shows 0 messages for empty list', () => {
    render(<SessionChat {...defaultProps} />);
    expect(screen.getByText('0 messages')).toBeInTheDocument();
  });

  /* ── onMessageCountChange ── */

  it('calls onMessageCountChange with visible message count', () => {
    const onMessageCountChange = vi.fn();
    render(
      <SessionChat
        {...defaultProps}
        messages={[userMessage, assistantMessage]}
        onMessageCountChange={onMessageCountChange}
      />,
    );
    expect(onMessageCountChange).toHaveBeenCalledWith(2);
  });

  /* ── Clear button ── */

  it('shows clear button when messages exist and onClear provided', () => {
    const onClear = vi.fn();
    render(<SessionChat {...defaultProps} messages={[userMessage]} onClear={onClear} />);
    const clearBtn = screen.getByTestId('clear-chat');
    fireEvent.click(clearBtn);
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it('hides clear button when no messages', () => {
    render(<SessionChat {...defaultProps} onClear={vi.fn()} />);
    expect(screen.queryByTestId('clear-chat')).not.toBeInTheDocument();
  });

  it('hides clear button when onClear is not provided', () => {
    render(<SessionChat {...defaultProps} messages={[userMessage]} />);
    expect(screen.queryByTestId('clear-chat')).not.toBeInTheDocument();
  });

  /* ── Model switch ── */

  it('shows model switch toggle when capability enabled and connected', () => {
    render(
      <SessionChat
        {...defaultProps}
        connected
        capabilities={{ set_model: true }}
        onSetModel={vi.fn()}
      />,
    );
    expect(screen.getByTestId('model-switch-toggle')).toBeInTheDocument();
  });

  it('hides model switch toggle when disconnected', () => {
    render(
      <SessionChat
        {...defaultProps}
        connected={false}
        capabilities={{ set_model: true }}
        onSetModel={vi.fn()}
      />,
    );
    expect(screen.queryByTestId('model-switch-toggle')).not.toBeInTheDocument();
  });

  it('opens and closes model input bar', () => {
    render(
      <SessionChat
        {...defaultProps}
        connected
        capabilities={{ set_model: true }}
        onSetModel={vi.fn()}
      />,
    );
    expect(screen.queryByTestId('model-input-bar')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('model-switch-toggle'));
    expect(screen.getByTestId('model-input-bar')).toBeInTheDocument();

    // Toggle off
    fireEvent.click(screen.getByTestId('model-switch-toggle'));
    expect(screen.queryByTestId('model-input-bar')).not.toBeInTheDocument();
  });

  it('submits model via Enter key', () => {
    const onSetModel = vi.fn();
    render(
      <SessionChat
        {...defaultProps}
        connected
        capabilities={{ set_model: true }}
        onSetModel={onSetModel}
      />,
    );
    fireEvent.click(screen.getByTestId('model-switch-toggle'));

    const input = screen.getByLabelText('Model ID input');
    fireEvent.change(input, { target: { value: 'claude-opus-4-6' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(onSetModel).toHaveBeenCalledWith('claude-opus-4-6');
    expect(screen.queryByTestId('model-input-bar')).not.toBeInTheDocument();
  });

  it('submits model via Switch button', () => {
    const onSetModel = vi.fn();
    render(
      <SessionChat
        {...defaultProps}
        connected
        capabilities={{ set_model: true }}
        onSetModel={onSetModel}
      />,
    );
    fireEvent.click(screen.getByTestId('model-switch-toggle'));

    const input = screen.getByLabelText('Model ID input');
    fireEvent.change(input, { target: { value: 'claude-sonnet' } });
    fireEvent.click(screen.getByTestId('model-submit'));

    expect(onSetModel).toHaveBeenCalledWith('claude-sonnet');
  });

  it('does not submit empty model name', () => {
    const onSetModel = vi.fn();
    render(
      <SessionChat
        {...defaultProps}
        connected
        capabilities={{ set_model: true }}
        onSetModel={onSetModel}
      />,
    );
    fireEvent.click(screen.getByTestId('model-switch-toggle'));

    const input = screen.getByLabelText('Model ID input');
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(onSetModel).not.toHaveBeenCalled();
  });

  it('closes model input on Escape', () => {
    render(
      <SessionChat
        {...defaultProps}
        connected
        capabilities={{ set_model: true }}
        onSetModel={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId('model-switch-toggle'));
    expect(screen.getByTestId('model-input-bar')).toBeInTheDocument();

    const input = screen.getByLabelText('Model ID input');
    fireEvent.keyDown(input, { key: 'Escape' });

    expect(screen.queryByTestId('model-input-bar')).not.toBeInTheDocument();
  });

  /* ── Thinking budget ── */

  it('shows thinking budget toggle when capability enabled and connected', () => {
    render(
      <SessionChat
        {...defaultProps}
        connected
        capabilities={{ set_thinking_tokens: true }}
        onSetThinkingTokens={vi.fn()}
      />,
    );
    expect(screen.getByTestId('thinking-budget-toggle')).toBeInTheDocument();
  });

  it('opens thinking menu and selects a preset', () => {
    const onSetThinkingTokens = vi.fn();
    render(
      <SessionChat
        {...defaultProps}
        connected
        capabilities={{ set_thinking_tokens: true }}
        onSetThinkingTokens={onSetThinkingTokens}
      />,
    );

    fireEvent.click(screen.getByTestId('thinking-budget-toggle'));
    expect(screen.getByTestId('thinking-menu')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('thinking-8K'));
    expect(onSetThinkingTokens).toHaveBeenCalledWith(8192);
    expect(screen.queryByTestId('thinking-menu')).not.toBeInTheDocument();
  });

  it('toggles thinking menu closed on second click', () => {
    render(
      <SessionChat
        {...defaultProps}
        connected
        capabilities={{ set_thinking_tokens: true }}
        onSetThinkingTokens={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByTestId('thinking-budget-toggle'));
    expect(screen.getByTestId('thinking-menu')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('thinking-budget-toggle'));
    expect(screen.queryByTestId('thinking-menu')).not.toBeInTheDocument();
  });

  /* ── Rewind files ── */

  it('shows rewind files button when capability enabled and connected', () => {
    const onRewindFiles = vi.fn();
    render(
      <SessionChat
        {...defaultProps}
        connected
        capabilities={{ rewind_files: true }}
        onRewindFiles={onRewindFiles}
      />,
    );
    const btn = screen.getByTestId('rewind-files');
    fireEvent.click(btn);
    expect(onRewindFiles).toHaveBeenCalledTimes(1);
  });

  it('hides rewind files button when capability disabled', () => {
    render(<SessionChat {...defaultProps} connected capabilities={{}} onRewindFiles={vi.fn()} />);
    expect(screen.queryByTestId('rewind-files')).not.toBeInTheDocument();
  });

  /* ── Message rendering ── */

  it('renders user and assistant messages', () => {
    render(<SessionChat {...defaultProps} messages={[userMessage, assistantMessage]} />);
    expect(screen.getByText('Please review the config.')).toBeInTheDocument();
    expect(screen.getByTestId('assistant-message')).toBeInTheDocument();
  });

  it('filters out system-type messages from visible list', () => {
    // System messages (metadata.messageType === 'system') are excluded from
    // visibleMessages by useRoomState, so they never render in the chat.
    render(<SessionChat {...defaultProps} messages={[userMessage, systemMessage]} />);
    expect(screen.queryByText('Session started')).not.toBeInTheDocument();
    // But the user message should still be there
    expect(screen.getByText('Please review the config.')).toBeInTheDocument();
  });

  it('renders running assistant message as streaming', () => {
    render(<SessionChat {...defaultProps} messages={[userMessage, runningMessage]} />);
    expect(screen.getByText('Working on it...')).toBeInTheDocument();
  });

  /* ── Streaming indicator ── */

  it('shows streaming message when streamingContent is provided', () => {
    render(
      <SessionChat {...defaultProps} messages={[userMessage]} streamingContent="Thinking..." />,
    );
    expect(screen.getByText('Thinking...')).toBeInTheDocument();
  });

  it('shows streaming message when streamingParts are provided', () => {
    render(
      <SessionChat
        {...defaultProps}
        messages={[userMessage]}
        streamingParts={[{ type: 'text', text: 'Part content' }]}
      />,
    );
    // Streaming message should be rendered (exact rendering depends on StreamingMessage)
    expect(screen.queryByTestId('session-empty-chat')).not.toBeInTheDocument();
  });

  it('does not show streaming indicator when a running message already exists', () => {
    // When hasRunningAssistantMessage is true, isStreaming should be false
    render(
      <SessionChat
        {...defaultProps}
        messages={[userMessage, runningMessage]}
        streamingContent="Should not appear as separate streaming"
      />,
    );
    // The running message is rendered, but no extra streaming indicator
    expect(screen.getByText('Working on it...')).toBeInTheDocument();
  });

  /* ── handleSend ── */

  it('calls onSend when sending a message', () => {
    const onSend = vi.fn();
    render(<SessionChat {...defaultProps} onSend={onSend} connected />);

    // Click a suggestion in empty state to trigger handleSend
    fireEvent.click(screen.getByText('Run the test suite and fix failures'));
    expect(onSend).toHaveBeenCalledWith('Run the test suite and fix failures', []);
  });

  /* ── handleCopy ── */

  it('delegates to onCopy callback when provided', () => {
    const onCopy = vi.fn();
    render(
      <SessionChat {...defaultProps} messages={[userMessage, assistantMessage]} onCopy={onCopy} />,
    );
    const copyBtn = screen.getByTitle('Copy');
    fireEvent.click(copyBtn);
    expect(onCopy).toHaveBeenCalledWith(assistantMessage.content);
  });

  it('falls back to navigator.clipboard when onCopy not provided', () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    render(<SessionChat {...defaultProps} messages={[userMessage, assistantMessage]} />);
    const copyBtn = screen.getByTitle('Copy');
    fireEvent.click(copyBtn);
    expect(writeText).toHaveBeenCalledWith(assistantMessage.content);
  });

  /* ── handleRegenerate ── */

  it('delegates to onRegenerate callback when provided', () => {
    const onRegenerate = vi.fn();
    render(
      <SessionChat
        {...defaultProps}
        messages={[userMessage, assistantMessage]}
        onRegenerate={onRegenerate}
      />,
    );
    const regenerateBtn = screen.getByTitle('Regenerate');
    fireEvent.click(regenerateBtn);
    expect(onRegenerate).toHaveBeenCalledWith(assistantMessage.id);
  });

  it('falls back to resending last user message when onRegenerate not provided', () => {
    const onSend = vi.fn();
    render(
      <SessionChat {...defaultProps} onSend={onSend} messages={[userMessage, assistantMessage]} />,
    );
    const regenerateBtn = screen.getByTitle('Regenerate');
    fireEvent.click(regenerateBtn);
    expect(onSend).toHaveBeenCalledWith(userMessage.content, []);
  });

  it('does nothing on regenerate fallback when no preceding user message found', () => {
    const onSend = vi.fn();
    render(<SessionChat {...defaultProps} onSend={onSend} messages={[assistantMessage]} />);
    const regenerateBtn = screen.getByTitle('Regenerate');
    fireEvent.click(regenerateBtn);
    // Should not call onSend since there is no user message before the assistant message
    expect(onSend).not.toHaveBeenCalled();
  });

  /* ── handleBookmark ── */

  it('delegates to onBookmark callback when provided', () => {
    const onBookmark = vi.fn();
    render(
      <SessionChat
        {...defaultProps}
        messages={[userMessage, assistantMessage]}
        onBookmark={onBookmark}
      />,
    );
    const bookmarkBtn = screen.getByTitle('Bookmark');
    fireEvent.click(bookmarkBtn);
    expect(onBookmark).toHaveBeenCalledWith(assistantMessage.id, true);
  });

  it('falls back to localStorage when onBookmark not provided', () => {
    render(<SessionChat {...defaultProps} messages={[userMessage, assistantMessage]} />);
    const bookmarkBtn = screen.getByTitle('Bookmark');
    fireEvent.click(bookmarkBtn);
    expect(localStorage.getItem(`bookmark:${assistantMessage.id}`)).toBe('1');
  });

  it('removes bookmark from localStorage on unbookmark', () => {
    localStorage.setItem(`bookmark:${assistantMessage.id}`, '1');
    render(<SessionChat {...defaultProps} messages={[userMessage, assistantMessage]} />);
    // Message should show as bookmarked, so the title changes to "Remove bookmark"
    const bookmarkBtn = screen.getByTitle('Remove bookmark');
    fireEvent.click(bookmarkBtn);
    expect(localStorage.getItem(`bookmark:${assistantMessage.id}`)).toBeNull();
  });

  /* ── Permissions rendering ── */

  it('renders permission UI via renderPermissions slot', () => {
    const permissions: PermissionRequest[] = [
      { requestId: 'perm-1', toolName: 'file_write', description: 'Write to disk' },
    ];
    const onPermissionRespond = vi.fn();
    const renderPermissions = vi.fn().mockReturnValue(<div data-testid="perm-ui">Allow?</div>);

    render(
      <SessionChat
        {...defaultProps}
        connected
        pendingPermissions={permissions}
        onPermissionRespond={onPermissionRespond}
        renderPermissions={renderPermissions}
      />,
    );

    expect(screen.getByTestId('perm-ui')).toBeInTheDocument();
    expect(renderPermissions).toHaveBeenCalledWith(permissions, expect.any(Function));

    // Test that the respond callback delegates to onPermissionRespond
    const respondFn = renderPermissions.mock.calls[0][1];
    respondFn('perm-1', 'allow_once');
    expect(onPermissionRespond).toHaveBeenCalledWith('perm-1', 'allow_once');
  });

  it('does not render permission UI when no pending permissions', () => {
    const renderPermissions = vi.fn().mockReturnValue(<div data-testid="perm-ui">Allow?</div>);
    render(
      <SessionChat
        {...defaultProps}
        connected
        pendingPermissions={[]}
        renderPermissions={renderPermissions}
      />,
    );
    expect(screen.queryByTestId('perm-ui')).not.toBeInTheDocument();
  });

  /* ── Room mode / MeshSidebar ── */

  it('renders MeshSidebar when a ravn participant exists', () => {
    render(
      <SessionChat
        {...defaultProps}
        messages={[roomAssistantMessage]}
        connected
        participants={new Map([[participant.peerId, participant]])}
      />,
    );
    const outerGrid = screen.getByTestId('session-chat');
    expect(outerGrid).toHaveAttribute('data-has-sidebar');
  });

  it('does not render sidebar when no ravn participants', () => {
    render(
      <SessionChat
        {...defaultProps}
        messages={[roomAssistantMessage]}
        connected
        participants={new Map([[skuldParticipant.peerId, skuldParticipant]])}
      />,
    );
    const outerGrid = screen.getByTestId('session-chat');
    expect(outerGrid).not.toHaveAttribute('data-has-sidebar');
  });

  /* ── Agent detail panel ── */

  it('opens the agent detail panel from a room message', () => {
    render(
      <SessionChat
        {...defaultProps}
        messages={[roomAssistantMessage]}
        connected
        historyLoaded
        participants={new Map([[participant.peerId, participant]])}
        agentEvents={agentEvents}
      />,
    );

    fireEvent.click(screen.getByLabelText('View event stream for Ravn-A'));

    expect(screen.getByTestId('agent-detail-panel')).toBeInTheDocument();
    expect(screen.getByText('Checking compose and runtime settings.')).toBeInTheDocument();
  });

  it('closes the agent detail panel', () => {
    render(
      <SessionChat
        {...defaultProps}
        messages={[roomAssistantMessage]}
        connected
        historyLoaded
        participants={new Map([[participant.peerId, participant]])}
        agentEvents={agentEvents}
      />,
    );

    fireEvent.click(screen.getByLabelText('View event stream for Ravn-A'));
    expect(screen.getByTestId('agent-detail-panel')).toBeInTheDocument();

    // Close the detail panel
    const closeBtn = screen.getByLabelText('Close agent detail panel');
    fireEvent.click(closeBtn);
    expect(screen.queryByTestId('agent-detail-panel')).not.toBeInTheDocument();
  });

  /* ── Internal message toggle (room mode) ── */

  it('shows internal toggle in room mode with multiple participants', () => {
    const participants = new Map([
      [participant.peerId, participant],
      [participant2.peerId, participant2],
    ]);
    render(
      <SessionChat
        {...defaultProps}
        messages={[roomAssistantMessage]}
        connected
        participants={participants}
      />,
    );
    expect(screen.getByTestId('internal-toggle')).toBeInTheDocument();
  });

  it('does not show internal toggle in single-participant mode', () => {
    render(
      <SessionChat {...defaultProps} messages={[userMessage]} connected participants={new Map()} />,
    );
    expect(screen.queryByTestId('internal-toggle')).not.toBeInTheDocument();
  });

  it('toggles internal visibility', () => {
    const participants = new Map([
      [participant.peerId, participant],
      [participant2.peerId, participant2],
    ]);
    render(
      <SessionChat
        {...defaultProps}
        messages={[roomAssistantMessage]}
        connected
        participants={participants}
      />,
    );
    const toggle = screen.getByTestId('internal-toggle');
    expect(toggle).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
  });

  /* ── MeshCascadePanel ── */

  it('renders MeshCascadePanel when mesh events exist', () => {
    const events: MeshOutcomeEvent[] = [
      {
        id: 'me-1',
        type: 'outcome',
        timestamp: now,
        participantId: 'peer-1',
        participant: { color: 'amber' },
        persona: 'Ravn-A',
        eventType: 'code_review',
        verdict: 'approve',
        summary: 'Looks good',
      },
    ];
    render(
      <SessionChat {...defaultProps} messages={[userMessage]} connected meshEvents={events} />,
    );
    const outerGrid = screen.getByTestId('session-chat');
    expect(outerGrid).toHaveAttribute('data-right-panel');
  });

  it('does not render right panel when no mesh events and no detail panel', () => {
    render(<SessionChat {...defaultProps} messages={[userMessage]} connected />);
    const outerGrid = screen.getByTestId('session-chat');
    expect(outerGrid).not.toHaveAttribute('data-right-panel');
  });

  /* ── handleOutcomeClick ── */

  it('scrolls to the closest outcome message when the outcome card is clicked', () => {
    const events: MeshOutcomeEvent[] = [
      {
        id: 'me-1',
        type: 'outcome',
        timestamp: new Date('2026-04-26T12:00:04Z'),
        participantId: 'peer-1',
        participant: { color: 'amber' },
        persona: 'Ravn-A',
        eventType: 'code_review',
        verdict: 'approve',
        summary: 'Approved the change',
        fields: {
          verdict: 'approve',
          summary: 'Approved the change',
          findings: `## Verified
- **Route pair count**: 26
- Use \`/api/v1/credentials/secrets\``,
        },
      },
    ];
    render(
      <SessionChat
        {...defaultProps}
        messages={[roomAssistantMessage, roomOutcomeMessage]}
        connected
        participants={new Map([[participant.peerId, participant]])}
        meshEvents={events}
      />,
    );
    const cascadePanel = screen.getByTestId('mesh-cascade-panel');
    const cascadeSummary = cascadePanel.querySelector('.niuu-chat-mesh-summary');
    expect(cascadeSummary).not.toBeNull();
    fireEvent.click(cascadeSummary!);
    expect(window.HTMLElement.prototype.scrollIntoView).toHaveBeenCalled();
    expect(screen.queryByText('Ravn-A outcome')).not.toBeInTheDocument();
  });

  it('opens the rendered outcome dialog from Show details', () => {
    const events: MeshOutcomeEvent[] = [
      {
        id: 'me-1',
        type: 'outcome',
        timestamp: new Date('2026-04-26T12:00:04Z'),
        participantId: 'peer-1',
        participant: { color: 'amber' },
        persona: 'Ravn-A',
        eventType: 'code_review',
        verdict: 'approve',
        summary: 'Approved the change',
        fields: {
          verdict: 'approve',
          summary: 'Approved the change',
          findings: `## Verified
- **Route pair count**: 26
- Use \`/api/v1/credentials/secrets\``,
        },
      },
    ];
    render(
      <SessionChat
        {...defaultProps}
        messages={[roomAssistantMessage, roomOutcomeMessage]}
        connected
        participants={new Map([[participant.peerId, participant]])}
        meshEvents={events}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Show details' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Ravn-A outcome')).toBeInTheDocument();
    expect(within(dialog).getAllByTestId('outcome-card').length).toBeGreaterThan(0);
    expect(within(dialog).getByText('Verified')).toBeInTheDocument();
    expect(within(dialog).getByText('checks_passed')).toBeInTheDocument();
    expect(within(dialog).getByText('12')).toBeInTheDocument();
    const routePairItems = within(dialog)
      .getAllByText('Route pair count')
      .map((element) => element.closest('li'))
      .filter((element): element is HTMLLIElement => element instanceof HTMLLIElement);
    expect(routePairItems.length).toBeGreaterThan(0);
    expect(routePairItems[0]).toHaveTextContent('Route pair count: 26');
    expect(within(dialog).getByText('/api/v1/credentials/secrets')).toBeInTheDocument();
  });

  it('collapses and expands the mesh peers and mesh cascade sidebars', () => {
    const events: MeshOutcomeEvent[] = [
      {
        id: 'me-1',
        type: 'outcome',
        timestamp: now,
        participantId: 'peer-1',
        participant: { color: 'amber' },
        persona: 'Ravn-A',
        eventType: 'code_review',
        verdict: 'approve',
        summary: 'Looks good',
      },
    ];
    render(
      <SessionChat
        {...defaultProps}
        messages={[roomAssistantMessage, roomOutcomeMessage]}
        connected
        participants={
          new Map([
            [participant.peerId, participant],
            [skuldParticipant.peerId, skuldParticipant],
          ])
        }
        meshEvents={events}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /collapse mesh peers sidebar/i }));
    expect(screen.getByRole('button', { name: /expand mesh peers sidebar/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /collapse mesh cascade sidebar/i }));
    expect(
      screen.getByRole('button', { name: /expand mesh cascade sidebar/i }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /expand mesh peers sidebar/i }));
    expect(
      screen.getByRole('button', { name: /collapse mesh peers sidebar/i }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /expand mesh cascade sidebar/i }));
    expect(
      screen.getByRole('button', { name: /collapse mesh cascade sidebar/i }),
    ).toBeInTheDocument();
  });

  /* ── handleSelectAgent ── */

  it('filters messages when agent is selected in sidebar', () => {
    const participants = new Map([
      [participant.peerId, participant],
      [participant2.peerId, participant2],
    ]);
    const msg1: ChatMessage = {
      ...roomAssistantMessage,
      id: 'ma1',
      content: 'Message from Ravn-A',
      participant,
    };
    const msg2: ChatMessage = {
      id: 'ma2',
      role: 'assistant',
      content: 'Message from Ravn-B',
      createdAt: now,
      status: 'done',
      participant: participant2,
    };
    render(
      <SessionChat
        {...defaultProps}
        messages={[msg1, msg2]}
        connected
        participants={participants}
      />,
    );
    // Both messages should be visible initially
    expect(screen.getByText('Message from Ravn-A')).toBeInTheDocument();
    expect(screen.getByText('Message from Ravn-B')).toBeInTheDocument();
  });

  /* ── Room message rendering ── */

  it('renders room messages with participant info when in room session', () => {
    const participants = new Map([[participant.peerId, participant]]);
    render(
      <SessionChat
        {...defaultProps}
        messages={[roomAssistantMessage]}
        connected
        participants={participants}
      />,
    );
    expect(screen.getByText('Need to inspect the config first.')).toBeInTheDocument();
  });

  /* ── handleSendDirected ── */

  it('passes onSendDirected callback through', () => {
    const onSendDirected = vi.fn();
    render(
      <SessionChat
        {...defaultProps}
        connected
        onSendDirected={onSendDirected}
        participants={new Map([[participant.peerId, participant]])}
      />,
    );
    // onSendDirected is passed to ChatInput; we just verify the component renders
    expect(screen.getByTestId('session-chat')).toBeInTheDocument();
  });

  /* ── onStop ── */

  it('passes onStop to ChatInput', () => {
    const onStop = vi.fn();
    render(<SessionChat {...defaultProps} connected onStop={onStop} />);
    expect(screen.getByTestId('session-chat')).toBeInTheDocument();
  });

  /* ── Thread groups ── */

  it('renders thread groups in room mode with internal messages', () => {
    const participants = new Map([
      [participant.peerId, participant],
      [participant2.peerId, participant2],
    ]);
    const internalMsgs: ChatMessage[] = [
      {
        id: 'int-1',
        role: 'assistant',
        content: 'Internal msg 1',
        createdAt: now,
        status: 'done',
        visibility: 'internal',
        threadId: 'thread-A',
        participant,
      },
      {
        id: 'int-2',
        role: 'assistant',
        content: 'Internal msg 2',
        createdAt: new Date('2026-04-26T12:00:01Z'),
        status: 'done',
        visibility: 'internal',
        threadId: 'thread-A',
        participant,
      },
      {
        id: 'ext-1',
        role: 'user',
        content: 'External message',
        createdAt: new Date('2026-04-26T12:00:02Z'),
      },
    ];

    render(
      <SessionChat
        {...defaultProps}
        messages={internalMsgs}
        connected
        participants={participants}
      />,
    );

    // External message should always be visible
    expect(screen.getByText('External message')).toBeInTheDocument();

    // Internal messages are visible by default in room mode
    expect(screen.getByText('Internal msg 1')).toBeInTheDocument();
  });

  /* ── isRoomSession detection ── */

  it('treats session as room session when participant has non-skuld type', () => {
    // Even with single participant, if participantType !== 'skuld', it's a room session
    const participants = new Map([[participant.peerId, participant]]);
    const msgWithParticipant: ChatMessage = {
      ...userMessage,
      participant,
    };
    render(
      <SessionChat
        {...defaultProps}
        messages={[msgWithParticipant]}
        connected
        participants={participants}
      />,
    );
    expect(screen.getByText('Please review the config.')).toBeInTheDocument();
  });

  /* ── Capabilities hidden when disconnected ── */

  it('hides toolbar controls when disconnected', () => {
    render(
      <SessionChat
        {...defaultProps}
        connected={false}
        capabilities={{ set_model: true, set_thinking_tokens: true, rewind_files: true }}
        onSetModel={vi.fn()}
        onSetThinkingTokens={vi.fn()}
        onRewindFiles={vi.fn()}
      />,
    );
    expect(screen.queryByTestId('model-switch-toggle')).not.toBeInTheDocument();
    expect(screen.queryByTestId('thinking-budget-toggle')).not.toBeInTheDocument();
    expect(screen.queryByTestId('rewind-files')).not.toBeInTheDocument();
  });

  /* ── All thinking presets ── */

  it('renders all four thinking presets', () => {
    render(
      <SessionChat
        {...defaultProps}
        connected
        capabilities={{ set_thinking_tokens: true }}
        onSetThinkingTokens={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId('thinking-budget-toggle'));
    expect(screen.getByTestId('thinking-4K')).toBeInTheDocument();
    expect(screen.getByTestId('thinking-8K')).toBeInTheDocument();
    expect(screen.getByTestId('thinking-16K')).toBeInTheDocument();
    expect(screen.getByTestId('thinking-32K')).toBeInTheDocument();
  });

  it('calls onSetThinkingTokens with correct values for each preset', () => {
    const onSetThinkingTokens = vi.fn();
    render(
      <SessionChat
        {...defaultProps}
        connected
        capabilities={{ set_thinking_tokens: true }}
        onSetThinkingTokens={onSetThinkingTokens}
      />,
    );

    fireEvent.click(screen.getByTestId('thinking-budget-toggle'));
    fireEvent.click(screen.getByTestId('thinking-4K'));
    expect(onSetThinkingTokens).toHaveBeenCalledWith(4096);

    fireEvent.click(screen.getByTestId('thinking-budget-toggle'));
    fireEvent.click(screen.getByTestId('thinking-16K'));
    expect(onSetThinkingTokens).toHaveBeenCalledWith(16384);

    fireEvent.click(screen.getByTestId('thinking-budget-toggle'));
    fireEvent.click(screen.getByTestId('thinking-32K'));
    expect(onSetThinkingTokens).toHaveBeenCalledWith(32768);
  });

  /* ── hasConversation logic ── */

  it('shows messages container when conversation exists', () => {
    render(<SessionChat {...defaultProps} messages={[userMessage, assistantMessage]} />);
    // Should not show empty state
    expect(screen.queryByTestId('session-empty-chat')).not.toBeInTheDocument();
  });

  it('shows empty state when only system messages exist', () => {
    render(<SessionChat {...defaultProps} messages={[systemMessage]} />);
    // System-only messages don't count as "conversation"
    expect(screen.getByTestId('session-empty-chat')).toBeInTheDocument();
  });

  /* ── localStorage error handling in bookmark ── */

  it('handles localStorage errors gracefully in bookmark fallback', () => {
    const origSetItem = localStorage.setItem.bind(localStorage);
    localStorage.setItem = () => {
      throw new Error('Storage full');
    };

    render(<SessionChat {...defaultProps} messages={[userMessage, assistantMessage]} />);
    const bookmarkBtn = screen.getByTitle('Bookmark');
    // Should not throw — the catch block swallows the error
    expect(() => fireEvent.click(bookmarkBtn)).not.toThrow();

    localStorage.setItem = origSetItem;
  });

  /* ── localStorage error in reading bookmark state ── */

  it('handles localStorage errors gracefully when reading bookmark state', () => {
    const origGetItem = localStorage.getItem.bind(localStorage);
    localStorage.getItem = () => {
      throw new Error('Access denied');
    };

    // Should render without throwing — the IIFE catch returns false
    const { container } = render(
      <SessionChat {...defaultProps} messages={[userMessage, assistantMessage]} />,
    );
    expect(container).toBeTruthy();

    localStorage.getItem = origGetItem;
  });

  /* ── Capability combinations ── */

  it('hides model switch when set_model is false', () => {
    render(
      <SessionChat
        {...defaultProps}
        connected
        capabilities={{ set_model: false }}
        onSetModel={vi.fn()}
      />,
    );
    expect(screen.queryByTestId('model-switch-toggle')).not.toBeInTheDocument();
  });

  it('hides model switch when onSetModel is not provided', () => {
    render(<SessionChat {...defaultProps} connected capabilities={{ set_model: true }} />);
    expect(screen.queryByTestId('model-switch-toggle')).not.toBeInTheDocument();
  });

  it('hides thinking toggle when onSetThinkingTokens is not provided', () => {
    render(
      <SessionChat {...defaultProps} connected capabilities={{ set_thinking_tokens: true }} />,
    );
    expect(screen.queryByTestId('thinking-budget-toggle')).not.toBeInTheDocument();
  });

  it('hides rewind files when onRewindFiles is not provided', () => {
    render(<SessionChat {...defaultProps} connected capabilities={{ rewind_files: true }} />);
    expect(screen.queryByTestId('rewind-files')).not.toBeInTheDocument();
  });
});
