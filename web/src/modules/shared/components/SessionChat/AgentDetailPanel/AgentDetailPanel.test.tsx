import { render, screen, fireEvent } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { AgentDetailPanel } from './AgentDetailPanel';
import type {
  ParticipantMeta,
  SkuldChatMessage,
  AgentInternalEvent,
} from '@/modules/shared/hooks/useSkuldChat';

// ── Mock useAgentDetail ───────────────────────────────────────────────

vi.mock('@/modules/shared/hooks/useAgentDetail', () => ({
  useAgentDetail: vi.fn(),
}));

// ── Mock child message components ─────────────────────────────────────

vi.mock('../ChatMessages', () => ({
  AssistantMessage: ({ message }: { message: SkuldChatMessage }) => (
    <div data-testid="assistant-message">{message.content}</div>
  ),
  StreamingMessage: ({ content }: { content: string }) => (
    <div data-testid="streaming-message">{content}</div>
  ),
  SystemMessage: ({ message }: { message: SkuldChatMessage }) => (
    <div data-testid="system-message">{message.content}</div>
  ),
  UserMessage: ({ message }: { message: SkuldChatMessage }) => (
    <div data-testid="user-message">{message.content}</div>
  ),
}));

import { useAgentDetail } from '@/modules/shared/hooks/useAgentDetail';

// ── Helpers ───────────────────────────────────────────────────────────

function makeParticipant(overrides: Partial<ParticipantMeta> = {}): ParticipantMeta {
  return {
    peerId: 'ravn-1',
    persona: 'Ravn Alpha',
    displayName: '',
    color: 'p2',
    participantType: 'ravn',
    gatewayUrl: 'http://ravn-1:8080',
    ...overrides,
  };
}

function _makeMessage(overrides: Partial<SkuldChatMessage> = {}): SkuldChatMessage {
  return {
    id: 'msg-1',
    role: 'assistant',
    content: 'Thinking about it',
    createdAt: new Date(),
    status: 'complete',
    ...overrides,
  };
}

function setupDetailMock(
  options: {
    messages?: SkuldChatMessage[];
    connected?: boolean;
    isRunning?: boolean;
  } = {}
) {
  vi.mocked(useAgentDetail).mockReturnValue({
    messages: options.messages ?? [],
    connected: options.connected ?? false,
    isRunning: options.isRunning ?? false,
  });
}

function makeEvent(overrides: Partial<AgentInternalEvent> = {}): AgentInternalEvent {
  return {
    id: `evt-${Math.random().toString(36).slice(2, 8)}`,
    participantId: 'ravn-1',
    timestamp: new Date(),
    frameType: 'thought',
    data: 'some data',
    metadata: {},
    ...overrides,
  };
}

function makeRoomParticipant(overrides: Partial<ParticipantMeta> & { status?: string } = {}) {
  const { status = 'idle', ...rest } = overrides;
  return {
    ...makeParticipant(rest),
    status: status as 'idle' | 'thinking' | 'tool_executing' | 'busy',
    joinedAt: new Date(),
  };
}

// ── Tests ─────────────────────────────────────────────────────────────

describe('AgentDetailPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('Header', () => {
    it('renders persona name', () => {
      setupDetailMock();
      render(
        <AgentDetailPanel
          participant={{ ...makeParticipant(), status: 'idle' as const, joinedAt: new Date() }}
          events={[]}
          onClose={vi.fn()}
        />
      );
      expect(screen.getByTestId('agent-persona-name')).toHaveTextContent('Ravn Alpha');
    });

    it('renders close button', () => {
      setupDetailMock();
      render(
        <AgentDetailPanel
          participant={{ ...makeParticipant(), status: 'idle' as const, joinedAt: new Date() }}
          events={[]}
          onClose={vi.fn()}
        />
      );
      expect(screen.getByTestId('agent-detail-close')).toBeInTheDocument();
    });

    it('calls onClose when close button is clicked', () => {
      setupDetailMock();
      const onClose = vi.fn();
      render(
        <AgentDetailPanel
          participant={{ ...makeParticipant(), status: 'idle' as const, joinedAt: new Date() }}
          events={[]}
          onClose={onClose}
        />
      );
      fireEvent.click(screen.getByTestId('agent-detail-close'));
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('calls onClose when Escape key is pressed', () => {
      setupDetailMock();
      const onClose = vi.fn();
      render(
        <AgentDetailPanel
          participant={{ ...makeParticipant(), status: 'idle' as const, joinedAt: new Date() }}
          events={[]}
          onClose={onClose}
        />
      );
      fireEvent.keyDown(document, { key: 'Escape' });
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('does not call onClose when Escape is pressed inside an input', () => {
      setupDetailMock();
      const onClose = vi.fn();
      render(
        <AgentDetailPanel
          participant={{ ...makeParticipant(), status: 'idle' as const, joinedAt: new Date() }}
          events={[]}
          onClose={onClose}
        />
      );
      const input = document.createElement('input');
      document.body.appendChild(input);
      input.focus();
      fireEvent.keyDown(input, { key: 'Escape', target: input });
      expect(onClose).not.toHaveBeenCalled();
      document.body.removeChild(input);
    });

    it('does not call onClose when Escape is pressed inside a textarea', () => {
      setupDetailMock();
      const onClose = vi.fn();
      render(
        <AgentDetailPanel
          participant={{ ...makeParticipant(), status: 'idle' as const, joinedAt: new Date() }}
          events={[]}
          onClose={onClose}
        />
      );
      const textarea = document.createElement('textarea');
      document.body.appendChild(textarea);
      textarea.focus();
      fireEvent.keyDown(textarea, { key: 'Escape', target: textarea });
      expect(onClose).not.toHaveBeenCalled();
      document.body.removeChild(textarea);
    });

    it('shows idle activity status when not running', () => {
      setupDetailMock({ isRunning: false });
      render(
        <AgentDetailPanel
          participant={{ ...makeParticipant(), status: 'idle' as const, joinedAt: new Date() }}
          events={[]}
          onClose={vi.fn()}
        />
      );
      expect(screen.getByTestId('agent-activity-status')).toHaveTextContent('idle');
    });

    it('shows thinking activity status', () => {
      setupDetailMock();
      render(
        <AgentDetailPanel
          participant={{ ...makeParticipant(), status: 'thinking' as const, joinedAt: new Date() }}
          events={[]}
          onClose={vi.fn()}
        />
      );
      expect(screen.getByTestId('agent-activity-status')).toHaveTextContent('thinking');
    });

    it('shows running tool activity status', () => {
      setupDetailMock();
      render(
        <AgentDetailPanel
          participant={{
            ...makeParticipant(),
            status: 'tool_executing' as const,
            joinedAt: new Date(),
          }}
          events={[]}
          onClose={vi.fn()}
        />
      );
      expect(screen.getByTestId('agent-activity-status')).toHaveTextContent('running tool');
    });

    it('shows raw status for unknown status values', () => {
      setupDetailMock();
      render(
        <AgentDetailPanel
          participant={{
            ...makeParticipant(),
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            status: 'custom_status' as any,
            joinedAt: new Date(),
          }}
          events={[]}
          onClose={vi.fn()}
        />
      );
      expect(screen.getByTestId('agent-activity-status')).toHaveTextContent('custom_status');
    });

    it('shows displayName with persona when displayName is set', () => {
      setupDetailMock();
      render(
        <AgentDetailPanel
          participant={{
            ...makeParticipant({ displayName: 'Alpha Bot', persona: 'Ravn Alpha' }),
            status: 'idle' as const,
            joinedAt: new Date(),
          }}
          events={[]}
          onClose={vi.fn()}
        />
      );
      expect(screen.getByTestId('agent-persona-name')).toHaveTextContent('Alpha Bot (Ravn Alpha)');
    });

    it('does not call onClose for non-Escape keys', () => {
      setupDetailMock();
      const onClose = vi.fn();
      render(
        <AgentDetailPanel
          participant={{ ...makeParticipant(), status: 'idle' as const, joinedAt: new Date() }}
          events={[]}
          onClose={onClose}
        />
      );
      fireEvent.keyDown(document, { key: 'Enter' });
      expect(onClose).not.toHaveBeenCalled();
    });

    it('does not call onClose when Escape is pressed inside a select', () => {
      setupDetailMock();
      const onClose = vi.fn();
      render(
        <AgentDetailPanel
          participant={{ ...makeParticipant(), status: 'idle' as const, joinedAt: new Date() }}
          events={[]}
          onClose={onClose}
        />
      );
      const select = document.createElement('select');
      document.body.appendChild(select);
      select.focus();
      fireEvent.keyDown(select, { key: 'Escape', target: select });
      expect(onClose).not.toHaveBeenCalled();
      document.body.removeChild(select);
    });
  });

  describe('Event rendering', () => {
    it('shows empty state when no events', () => {
      setupDetailMock();
      render(
        <AgentDetailPanel
          participant={{ ...makeParticipant(), status: 'idle' as const, joinedAt: new Date() }}
          events={[]}
          onClose={vi.fn()}
        />
      );
      expect(screen.getByTestId('agent-detail-empty')).toBeInTheDocument();
    });

    it('renders thought events', () => {
      setupDetailMock();
      render(
        <AgentDetailPanel
          participant={{ ...makeParticipant(), status: 'idle' as const, joinedAt: new Date() }}
          events={[
            {
              id: 'evt-1',
              participantId: 'ravn-1',
              timestamp: new Date(),
              frameType: 'thought',
              data: 'I should check the file',
              metadata: {},
            },
          ]}
          onClose={vi.fn()}
        />
      );
      expect(screen.getByText('thinking')).toBeInTheDocument();
      expect(screen.getByText('I should check the file')).toBeInTheDocument();
    });

    it('renders thought events with object data', () => {
      setupDetailMock();
      render(
        <AgentDetailPanel
          participant={{ ...makeParticipant(), status: 'idle' as const, joinedAt: new Date() }}
          events={[
            {
              id: 'evt-1',
              participantId: 'ravn-1',
              timestamp: new Date(),
              frameType: 'thought',
              data: { reasoning: 'complex' },
              metadata: {},
            },
          ]}
          onClose={vi.fn()}
        />
      );
      expect(screen.getByText('thinking')).toBeInTheDocument();
    });

    it('renders tool_start events with tool_name metadata', () => {
      setupDetailMock();
      render(
        <AgentDetailPanel
          participant={{ ...makeParticipant(), status: 'idle' as const, joinedAt: new Date() }}
          events={[
            {
              id: 'evt-2',
              participantId: 'ravn-1',
              timestamp: new Date(),
              frameType: 'tool_start',
              data: 'Read',
              metadata: { tool_name: 'Read', input: '/path/to/file' },
            },
          ]}
          onClose={vi.fn()}
        />
      );
      expect(screen.getByText('tool: Read')).toBeInTheDocument();
      expect(screen.getByText('/path/to/file')).toBeInTheDocument();
    });

    it('renders tool_start events without tool_name', () => {
      setupDetailMock();
      render(
        <AgentDetailPanel
          participant={{ ...makeParticipant(), status: 'idle' as const, joinedAt: new Date() }}
          events={[
            {
              id: 'evt-2',
              participantId: 'ravn-1',
              timestamp: new Date(),
              frameType: 'tool_start',
              data: 'Bash',
              metadata: {},
            },
          ]}
          onClose={vi.fn()}
        />
      );
      expect(screen.getByText('tool: Bash')).toBeInTheDocument();
    });

    it('renders tool_start events with object input', () => {
      setupDetailMock();
      render(
        <AgentDetailPanel
          participant={{ ...makeParticipant(), status: 'idle' as const, joinedAt: new Date() }}
          events={[
            {
              id: 'evt-2',
              participantId: 'ravn-1',
              timestamp: new Date(),
              frameType: 'tool_start',
              data: 'Edit',
              metadata: { tool_name: 'Edit', input: { file: 'main.ts', line: 42 } },
            },
          ]}
          onClose={vi.fn()}
        />
      );
      expect(screen.getByText('tool: Edit')).toBeInTheDocument();
    });

    it('renders tool_start events without input', () => {
      setupDetailMock();
      render(
        <AgentDetailPanel
          participant={{ ...makeParticipant(), status: 'idle' as const, joinedAt: new Date() }}
          events={[
            {
              id: 'evt-2',
              participantId: 'ravn-1',
              timestamp: new Date(),
              frameType: 'tool_start',
              data: 'Grep',
              metadata: { tool_name: 'Grep' },
            },
          ]}
          onClose={vi.fn()}
        />
      );
      expect(screen.getByText('tool: Grep')).toBeInTheDocument();
    });

    it('renders tool_result events with tool_name', () => {
      setupDetailMock();
      render(
        <AgentDetailPanel
          participant={{ ...makeParticipant(), status: 'idle' as const, joinedAt: new Date() }}
          events={[
            {
              id: 'evt-3',
              participantId: 'ravn-1',
              timestamp: new Date(),
              frameType: 'tool_result',
              data: 'File contents here',
              metadata: { tool_name: 'Read' },
            },
          ]}
          onClose={vi.fn()}
        />
      );
      expect(screen.getByText('result: Read')).toBeInTheDocument();
      expect(screen.getByText('File contents here')).toBeInTheDocument();
    });

    it('renders tool_result events without tool_name', () => {
      setupDetailMock();
      render(
        <AgentDetailPanel
          participant={{ ...makeParticipant(), status: 'idle' as const, joinedAt: new Date() }}
          events={[
            {
              id: 'evt-3',
              participantId: 'ravn-1',
              timestamp: new Date(),
              frameType: 'tool_result',
              data: 'output',
              metadata: {},
            },
          ]}
          onClose={vi.fn()}
        />
      );
      expect(screen.getByText('result:')).toBeInTheDocument();
    });

    it('renders unknown frameType events with fallback', () => {
      setupDetailMock();
      render(
        <AgentDetailPanel
          participant={{ ...makeParticipant(), status: 'idle' as const, joinedAt: new Date() }}
          events={[
            {
              id: 'evt-4',
              participantId: 'ravn-1',
              timestamp: new Date(),
              frameType: 'custom_frame',
              data: 'some data',
              metadata: {},
            },
          ]}
          onClose={vi.fn()}
        />
      );
      expect(screen.getByText('custom_frame')).toBeInTheDocument();
      expect(screen.getByText('some data')).toBeInTheDocument();
    });
  });

  describe('with different participant colors', () => {
    it('renders the panel for amber participants', () => {
      setupDetailMock();
      const participant = {
        ...makeParticipant({ color: 'p1', persona: 'Amber Agent' }),
        status: 'idle' as const,
        joinedAt: new Date(),
      };
      render(<AgentDetailPanel participant={participant} events={[]} onClose={vi.fn()} />);
      expect(screen.getByTestId('agent-persona-name')).toHaveTextContent('Amber Agent');
    });

    it('renders the panel for participants with unknown colors', () => {
      setupDetailMock();
      const participant = {
        ...makeParticipant({ color: 'unknown-color', persona: 'Mystery Agent' }),
        status: 'idle' as const,
        joinedAt: new Date(),
      };
      render(<AgentDetailPanel participant={participant} events={[]} onClose={vi.fn()} />);
      expect(screen.getByTestId('agent-persona-name')).toHaveTextContent('Mystery Agent');
    });
  });

  describe('Status label rendering', () => {
    it('shows "running tool" for tool_executing status', () => {
      setupDetailMock();
      render(
        <AgentDetailPanel
          participant={makeRoomParticipant({ status: 'tool_executing' })}
          events={[]}
          onClose={vi.fn()}
        />
      );
      expect(screen.getByTestId('agent-activity-status')).toHaveTextContent('running tool');
    });

    it('falls back to raw status string for unknown statuses', () => {
      setupDetailMock();
      render(
        <AgentDetailPanel
          participant={makeRoomParticipant({ status: 'busy' })}
          events={[]}
          onClose={vi.fn()}
        />
      );
      expect(screen.getByTestId('agent-activity-status')).toHaveTextContent('busy');
    });
  });

  describe('Event rendering with frame types', () => {
    it('renders a thought event', () => {
      setupDetailMock();
      const events = [makeEvent({ frameType: 'thought', data: 'I am reasoning' })];
      render(
        <AgentDetailPanel participant={makeRoomParticipant()} events={events} onClose={vi.fn()} />
      );
      expect(screen.queryByTestId('agent-detail-empty')).not.toBeInTheDocument();
      expect(screen.getByText('thinking')).toBeInTheDocument();
      expect(screen.getByText('I am reasoning')).toBeInTheDocument();
    });

    it('renders a tool_start event with tool_name from metadata', () => {
      setupDetailMock();
      const events = [
        makeEvent({
          frameType: 'tool_start',
          data: 'fallback-name',
          metadata: { tool_name: 'read_file', input: '{"path": "/tmp/test.txt"}' },
        }),
      ];
      render(
        <AgentDetailPanel participant={makeRoomParticipant()} events={events} onClose={vi.fn()} />
      );
      expect(screen.getByText('tool: read_file')).toBeInTheDocument();
      expect(screen.getByText('{"path": "/tmp/test.txt"}')).toBeInTheDocument();
    });

    it('renders a tool_start event falling back to data when no tool_name', () => {
      setupDetailMock();
      const events = [
        makeEvent({
          frameType: 'tool_start',
          data: 'some_tool',
          metadata: {},
        }),
      ];
      render(
        <AgentDetailPanel participant={makeRoomParticipant()} events={events} onClose={vi.fn()} />
      );
      expect(screen.getByText('tool: some_tool')).toBeInTheDocument();
    });

    it('renders a tool_start event with object input', () => {
      setupDetailMock();
      const events = [
        makeEvent({
          frameType: 'tool_start',
          data: 'tool',
          metadata: { tool_name: 'bash', input: { cmd: 'ls' } },
        }),
      ];
      render(
        <AgentDetailPanel participant={makeRoomParticipant()} events={events} onClose={vi.fn()} />
      );
      expect(screen.getByText('tool: bash')).toBeInTheDocument();
      // Object input is JSON-stringified inside a <pre>
      const pre = screen.getByText(
        (_content, el) => el?.tagName === 'PRE' && el.textContent?.includes('"cmd"') === true
      );
      expect(pre).toBeInTheDocument();
    });

    it('renders a tool_start event without input', () => {
      setupDetailMock();
      const events = [
        makeEvent({
          frameType: 'tool_start',
          data: 'tool',
          metadata: { tool_name: 'noop' },
        }),
      ];
      render(
        <AgentDetailPanel participant={makeRoomParticipant()} events={events} onClose={vi.fn()} />
      );
      expect(screen.getByText('tool: noop')).toBeInTheDocument();
    });

    it('renders a tool_result event with tool_name', () => {
      setupDetailMock();
      const events = [
        makeEvent({
          frameType: 'tool_result',
          data: 'success output',
          metadata: { tool_name: 'read_file' },
        }),
      ];
      render(
        <AgentDetailPanel participant={makeRoomParticipant()} events={events} onClose={vi.fn()} />
      );
      expect(screen.getByText('result: read_file')).toBeInTheDocument();
      expect(screen.getByText('success output')).toBeInTheDocument();
    });

    it('renders a tool_result event without tool_name', () => {
      setupDetailMock();
      const events = [
        makeEvent({
          frameType: 'tool_result',
          data: 'output data',
          metadata: {},
        }),
      ];
      render(
        <AgentDetailPanel participant={makeRoomParticipant()} events={events} onClose={vi.fn()} />
      );
      expect(screen.getByText('result:')).toBeInTheDocument();
      expect(screen.getByText('output data')).toBeInTheDocument();
    });

    it('renders an unknown frameType event as fallback', () => {
      setupDetailMock();
      const events = [
        makeEvent({
          frameType: 'custom_type',
          data: 'custom data',
        }),
      ];
      render(
        <AgentDetailPanel participant={makeRoomParticipant()} events={events} onClose={vi.fn()} />
      );
      expect(screen.getByText('custom_type')).toBeInTheDocument();
      expect(screen.getByText('custom data')).toBeInTheDocument();
    });

    it('serializes non-string event data as JSON', () => {
      setupDetailMock();
      const events = [
        makeEvent({
          frameType: 'thought',
          data: { key: 'value' },
        }),
      ];
      render(
        <AgentDetailPanel participant={makeRoomParticipant()} events={events} onClose={vi.fn()} />
      );
      const pre = screen.getByText(
        (_content, el) => el?.tagName === 'PRE' && el.textContent?.includes('"key"') === true
      );
      expect(pre).toBeInTheDocument();
    });

    it('renders multiple events', () => {
      setupDetailMock();
      const events = [
        makeEvent({ id: 'e1', frameType: 'thought', data: 'thought one' }),
        makeEvent({ id: 'e2', frameType: 'tool_start', data: 'tool_a', metadata: {} }),
        makeEvent({ id: 'e3', frameType: 'tool_result', data: 'result_a', metadata: {} }),
      ];
      render(
        <AgentDetailPanel participant={makeRoomParticipant()} events={events} onClose={vi.fn()} />
      );
      expect(screen.getByText('thought one')).toBeInTheDocument();
      expect(screen.getByText('tool: tool_a')).toBeInTheDocument();
      expect(screen.getByText('result_a')).toBeInTheDocument();
    });
  });

  describe('Scroll tracking', () => {
    it('handles scroll event and sets near-bottom state', () => {
      setupDetailMock();
      const events = [makeEvent({ id: 'e1' })];
      const { container } = render(
        <AgentDetailPanel participant={makeRoomParticipant()} events={events} onClose={vi.fn()} />
      );

      // Find the scroll container (messagesContainer)
      const scrollContainer = container.querySelector('[class*="messagesContainer"]');
      if (!scrollContainer) throw new Error('Scroll container not found');

      // Mock scroll properties to simulate being far from bottom
      Object.defineProperty(scrollContainer, 'scrollHeight', { value: 1000, configurable: true });
      Object.defineProperty(scrollContainer, 'scrollTop', { value: 0, configurable: true });
      Object.defineProperty(scrollContainer, 'clientHeight', { value: 400, configurable: true });

      fireEvent.scroll(scrollContainer);

      // dist = 1000 - 0 - 400 = 600, which is > SCROLL_THRESHOLD * 2 (300)
      // showScrollBtn should be true, so the scroll-to-bottom button should appear
      expect(screen.getByLabelText('Scroll to bottom')).toBeInTheDocument();
    });

    it('hides scroll button when near bottom', () => {
      setupDetailMock();
      const events = [makeEvent({ id: 'e1' })];
      const { container } = render(
        <AgentDetailPanel participant={makeRoomParticipant()} events={events} onClose={vi.fn()} />
      );

      const scrollContainer = container.querySelector('[class*="messagesContainer"]');
      if (!scrollContainer) throw new Error('Scroll container not found');

      // Simulate being near bottom: dist = 1000 - 900 - 90 = 10, which is < 150
      Object.defineProperty(scrollContainer, 'scrollHeight', { value: 1000, configurable: true });
      Object.defineProperty(scrollContainer, 'scrollTop', { value: 900, configurable: true });
      Object.defineProperty(scrollContainer, 'clientHeight', { value: 90, configurable: true });

      fireEvent.scroll(scrollContainer);

      expect(screen.queryByLabelText('Scroll to bottom')).not.toBeInTheDocument();
    });

    it('clicking scroll-to-bottom button invokes scrollIntoView', () => {
      setupDetailMock();
      const events = [makeEvent({ id: 'e1' })];
      const { container } = render(
        <AgentDetailPanel participant={makeRoomParticipant()} events={events} onClose={vi.fn()} />
      );

      const scrollContainer = container.querySelector('[class*="messagesContainer"]');
      if (!scrollContainer) throw new Error('Scroll container not found');

      // Scroll far from bottom to show button
      Object.defineProperty(scrollContainer, 'scrollHeight', { value: 1000, configurable: true });
      Object.defineProperty(scrollContainer, 'scrollTop', { value: 0, configurable: true });
      Object.defineProperty(scrollContainer, 'clientHeight', { value: 400, configurable: true });
      fireEvent.scroll(scrollContainer);

      const btn = screen.getByLabelText('Scroll to bottom');
      fireEvent.click(btn);
      // No error = scrollToBottom ran successfully
    });
  });

  describe('Auto-scroll on new events', () => {
    it('increments new count badge when not near bottom and events arrive', () => {
      setupDetailMock();
      const initialEvents = [makeEvent({ id: 'e1' })];
      const { container, rerender } = render(
        <AgentDetailPanel
          participant={makeRoomParticipant()}
          events={initialEvents}
          onClose={vi.fn()}
        />
      );

      const scrollContainer = container.querySelector('[class*="messagesContainer"]');
      if (!scrollContainer) throw new Error('Scroll container not found');

      // Simulate scrolling far from bottom
      Object.defineProperty(scrollContainer, 'scrollHeight', { value: 1000, configurable: true });
      Object.defineProperty(scrollContainer, 'scrollTop', { value: 0, configurable: true });
      Object.defineProperty(scrollContainer, 'clientHeight', { value: 400, configurable: true });
      fireEvent.scroll(scrollContainer);

      // Add new events via rerender
      const updatedEvents = [...initialEvents, makeEvent({ id: 'e2' }), makeEvent({ id: 'e3' })];

      rerender(
        <AgentDetailPanel
          participant={makeRoomParticipant()}
          events={updatedEvents}
          onClose={vi.fn()}
        />
      );

      // The scroll button should be visible with a count badge
      const btn = screen.getByLabelText('Scroll to bottom');
      expect(btn).toBeInTheDocument();
      expect(screen.getByText('2')).toBeInTheDocument();
    });

    it('resets new count when scrolled near bottom', () => {
      setupDetailMock();
      const initialEvents = [makeEvent({ id: 'e1' })];
      const { container, rerender } = render(
        <AgentDetailPanel
          participant={makeRoomParticipant()}
          events={initialEvents}
          onClose={vi.fn()}
        />
      );

      const scrollContainer = container.querySelector('[class*="messagesContainer"]');
      if (!scrollContainer) throw new Error('Scroll container not found');

      // Scroll far from bottom, add events to build up count
      Object.defineProperty(scrollContainer, 'scrollHeight', { value: 1000, configurable: true });
      Object.defineProperty(scrollContainer, 'scrollTop', { value: 0, configurable: true });
      Object.defineProperty(scrollContainer, 'clientHeight', { value: 400, configurable: true });
      fireEvent.scroll(scrollContainer);

      const updatedEvents = [...initialEvents, makeEvent({ id: 'e2' })];
      rerender(
        <AgentDetailPanel
          participant={makeRoomParticipant()}
          events={updatedEvents}
          onClose={vi.fn()}
        />
      );

      // Now scroll near bottom
      Object.defineProperty(scrollContainer, 'scrollTop', { value: 850, configurable: true });
      Object.defineProperty(scrollContainer, 'clientHeight', { value: 100, configurable: true });
      fireEvent.scroll(scrollContainer);

      // dist = 1000 - 850 - 100 = 50, which is <= 150
      // newCount should be reset to 0, no badge
      expect(screen.queryByText('1')).not.toBeInTheDocument();
    });
  });

  describe('Display name rendering', () => {
    it('shows displayName with persona in parentheses when displayName is set', () => {
      setupDetailMock();
      render(
        <AgentDetailPanel
          participant={makeRoomParticipant({ displayName: 'Agent Smith', persona: 'Ravn Alpha' })}
          events={[]}
          onClose={vi.fn()}
        />
      );
      expect(screen.getByTestId('agent-persona-name')).toHaveTextContent(
        'Agent Smith (Ravn Alpha)'
      );
    });
  });
});
