import { render, screen, fireEvent } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { RoomMessage } from './RoomMessage';
import type { SkuldChatMessage, ParticipantMeta } from '@/modules/shared/hooks/useSkuldChat';

// ── Mock child message components ────────────────────────────────────

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

// ── Helpers ──────────────────────────────────────────────────────────

function makeParticipant(overrides: Partial<ParticipantMeta> = {}): ParticipantMeta {
  return {
    peerId: 'ravn-1',
    persona: 'Ravn Alpha',
    color: 'p2',
    participantType: 'ravn',
    gatewayUrl: 'http://ravn-1:8080',
    ...overrides,
  };
}

function makeMessage(overrides: Partial<SkuldChatMessage> = {}): SkuldChatMessage {
  return {
    id: 'msg-1',
    role: 'assistant',
    content: 'Hello from agent',
    createdAt: new Date(),
    status: 'complete',
    ...overrides,
  };
}

// ── Tests ─────────────────────────────────────────────────────────────

describe('RoomMessage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('participant label', () => {
    it('renders participant label for Ravn messages', () => {
      const msg = makeMessage({ participant: makeParticipant() });
      render(<RoomMessage message={msg} />);
      expect(screen.getByTestId('participant-label')).toBeInTheDocument();
    });

    it('shows persona name in label', () => {
      const msg = makeMessage({ participant: makeParticipant({ persona: 'Agent X' }) });
      render(<RoomMessage message={msg} />);
      expect(screen.getByTestId('participant-label')).toHaveTextContent('Agent X');
    });

    it('does not render label for human participants', () => {
      const msg = makeMessage({
        participant: makeParticipant({ participantType: 'human' }),
      });
      render(<RoomMessage message={msg} />);
      expect(screen.queryByTestId('participant-label')).not.toBeInTheDocument();
    });

    it('does not render label when no participant metadata', () => {
      const msg = makeMessage({ participant: undefined });
      render(<RoomMessage message={msg} />);
      expect(screen.queryByTestId('participant-label')).not.toBeInTheDocument();
    });
  });

  describe('view detail button', () => {
    it('renders detail button for Ravn with gatewayUrl and onSelectAgent', () => {
      const msg = makeMessage({ participant: makeParticipant() });
      render(<RoomMessage message={msg} onSelectAgent={vi.fn()} />);
      expect(screen.getByTestId('view-agent-detail-btn')).toBeInTheDocument();
    });

    it('does not render detail button when no onSelectAgent prop', () => {
      const msg = makeMessage({ participant: makeParticipant() });
      render(<RoomMessage message={msg} />);
      expect(screen.queryByTestId('view-agent-detail-btn')).not.toBeInTheDocument();
    });

    it('does not render detail button when participant has no gatewayUrl', () => {
      const participant = makeParticipant({ gatewayUrl: undefined });
      const msg = makeMessage({ participant });
      render(<RoomMessage message={msg} onSelectAgent={vi.fn()} />);
      expect(screen.queryByTestId('view-agent-detail-btn')).not.toBeInTheDocument();
    });

    it('calls onSelectAgent with peerId when detail button is clicked', () => {
      const onSelectAgent = vi.fn();
      const participant = makeParticipant({ peerId: 'ravn-42' });
      const msg = makeMessage({ participant });
      render(<RoomMessage message={msg} onSelectAgent={onSelectAgent} />);
      fireEvent.click(screen.getByTestId('view-agent-detail-btn'));
      expect(onSelectAgent).toHaveBeenCalledWith('ravn-42');
    });

    it('calls onSelectAgent when participant label is clicked', () => {
      const onSelectAgent = vi.fn();
      const participant = makeParticipant({ peerId: 'ravn-42' });
      const msg = makeMessage({ participant });
      render(<RoomMessage message={msg} onSelectAgent={onSelectAgent} />);
      fireEvent.click(screen.getByTestId('participant-label'));
      expect(onSelectAgent).toHaveBeenCalledWith('ravn-42');
    });
  });

  describe('message component delegation', () => {
    it('renders AssistantMessage for complete assistant role', () => {
      const msg = makeMessage({ role: 'assistant', status: 'complete' });
      render(<RoomMessage message={msg} />);
      expect(screen.getByTestId('assistant-message')).toBeInTheDocument();
    });

    it('renders StreamingMessage for running assistant role', () => {
      const msg = makeMessage({ role: 'assistant', status: 'running' });
      render(<RoomMessage message={msg} />);
      expect(screen.getByTestId('streaming-message')).toBeInTheDocument();
    });

    it('renders UserMessage for user role', () => {
      const msg = makeMessage({ role: 'user', status: 'complete' });
      render(<RoomMessage message={msg} />);
      expect(screen.getByTestId('user-message')).toBeInTheDocument();
    });

    it('renders SystemMessage for system metadata messages', () => {
      const msg = makeMessage({
        role: 'assistant',
        status: 'complete',
        metadata: { messageType: 'system' },
      });
      render(<RoomMessage message={msg} />);
      expect(screen.getByTestId('system-message')).toBeInTheDocument();
    });
  });

  describe('participant color fallback', () => {
    it('renders with unknown color falling back to secondary', () => {
      const participant = makeParticipant({ color: 'unknown-color' });
      const msg = makeMessage({ participant });
      render(<RoomMessage message={msg} onSelectAgent={vi.fn()} />);
      // Verify the label still renders even with an unknown color
      expect(screen.getByTestId('participant-label')).toBeInTheDocument();
    });
  });

  describe('selected state', () => {
    it('marks participant label as selected when selectedAgentId matches', () => {
      const participant = makeParticipant({ peerId: 'ravn-1' });
      const msg = makeMessage({ participant });
      render(<RoomMessage message={msg} onSelectAgent={vi.fn()} selectedAgentId="ravn-1" />);
      expect(screen.getByTestId('participant-label')).toHaveAttribute('data-selected', 'true');
    });

    it('does not mark participant label as selected when id differs', () => {
      const participant = makeParticipant({ peerId: 'ravn-1' });
      const msg = makeMessage({ participant });
      render(<RoomMessage message={msg} onSelectAgent={vi.fn()} selectedAgentId="ravn-2" />);
      expect(screen.getByTestId('participant-label')).not.toHaveAttribute('data-selected');
    });
  });
});
