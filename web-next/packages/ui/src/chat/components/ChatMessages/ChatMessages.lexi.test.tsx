import { afterEach, describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AssistantMessage, StreamingMessage, SystemMessage, UserMessage } from './ChatMessages';
import type { ChatMessage, ChatMessagePart } from '../../types';

const now = new Date('2026-11-05T12:00:00Z');

afterEach(() => {
  localStorage.clear();
});

const assistant = (
  parts: ChatMessagePart[],
  extra: Partial<ChatMessage> = {},
  model = 'claude-sonnet-5-5',
): ChatMessage => ({
  id: 'a1',
  role: 'assistant',
  content: 'Done',
  createdAt: now,
  status: 'done',
  parts,
  metadata: { usage: { [model]: { inputTokens: 1, outputTokens: 2 } } },
  ...extra,
});

const user = (extra: Partial<ChatMessage> = {}): ChatMessage => ({
  id: 'u1',
  role: 'user',
  content: 'Also link NIU-1342 in the PR body.',
  createdAt: now,
  ...extra,
});

describe('assistant header', () => {
  it.each([
    ['claude-sonnet-5-5', 'var(--color-harness-claude)'],
    ['gpt-5-codex', 'var(--color-harness-codex)'],
    ['qwen3.8-27b', 'var(--color-brand)'],
  ])('shows an engine-coloured dot and the model name for %s', (model, ink) => {
    render(
      <AssistantMessage message={assistant([{ type: 'text', id: 't', text: 'Hi' }], {}, model)} />,
    );
    const speaker = screen.getByTestId('model-speaker');
    expect(speaker).toHaveTextContent(model);
    expect(speaker.style.getPropertyValue('--niuu-chat-engine')).toBe(ink);
  });

  it('shows no speaker when the model is unknown', () => {
    render(<AssistantMessage message={assistant([], { metadata: undefined })} />);
    expect(screen.queryByTestId('model-speaker')).toBeNull();
  });

  it('keeps the avatar preference', () => {
    localStorage.setItem('niuu.compactUx.showAgentAvatar', 'false');
    const { container } = render(<AssistantMessage message={assistant([])} />);
    expect(container.querySelector('.niuu-chat-avatar')).toBeNull();
  });

  it('colours the streaming header too', () => {
    render(<StreamingMessage content="partial" model="gpt-5" />);
    expect(screen.getByTestId('model-speaker')).toHaveTextContent('gpt-5');
  });
});

describe('commentary and final answer', () => {
  const parts: ChatMessagePart[] = [
    { type: 'text', id: 'c', phase: 'commentary', text: 'Checking the resolver.' },
    { type: 'tool_use', id: 'b', name: 'Bash', input: { command: 'ls' } },
    { type: 'tool_result', tool_use_id: 'b', content: 'x' },
    { type: 'text', id: 'f', phase: 'final_answer', text: 'Fixed it.' },
  ];

  it('dims commentary and tags the final answer once work precedes it', () => {
    const { container } = render(<AssistantMessage message={assistant(parts)} />);
    expect(container.querySelector('[data-text-id="c"]')).toHaveClass('niuu-chat-commentary');
    const answer = container.querySelector('[data-text-id="f"]');
    expect(answer).not.toHaveClass('niuu-chat-commentary');
    expect(answer).toContainElement(screen.getByTestId('final-tag'));
    expect(screen.getByTestId('final-tag')).toHaveTextContent('FINAL');
    expect(screen.getAllByTestId('final-tag')).toHaveLength(1);
  });

  it('does not tag a lone final answer', () => {
    render(
      <AssistantMessage
        message={assistant([{ type: 'text', id: 'f', phase: 'final_answer', text: 'Hi' }])}
      />,
    );
    expect(screen.queryByTestId('final-tag')).toBeNull();
  });

  it('renders text without a phase as today', () => {
    const { container } = render(
      <AssistantMessage message={assistant([{ type: 'text', id: 'p', text: 'Plain' }])} />,
    );
    expect(container.querySelector('[data-text-id="p"]')).not.toHaveClass('niuu-chat-commentary');
    expect(screen.queryByTestId('final-tag')).toBeNull();
  });
});

describe('tool calls in an assistant message', () => {
  it('renders a burst as one row and a lone call as a one-call row', () => {
    render(
      <AssistantMessage
        message={assistant([
          { type: 'tool_use', id: 'r1', name: 'Read', input: { file_path: 'a.ts' } },
          { type: 'tool_use', id: 'r2', name: 'Bash', input: { command: 'ls' } },
          { type: 'text', id: 't', text: 'Then' },
          { type: 'tool_use', id: 'g1', name: 'Grep', input: { pattern: 'p' } },
        ])}
      />,
    );
    const rows = screen.getAllByTestId('tool-group-block');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('1 file read · 1 command');
    expect(rows[1]).toHaveTextContent('Searched p');
  });

  it('spins the row while a streaming message has a call without a result', () => {
    render(
      <StreamingMessage
        content=""
        parts={[
          { type: 'tool_use', id: 'b1', name: 'Bash', input: { command: 'a' } },
          { type: 'tool_use', id: 'b2', name: 'Bash', input: { command: 'pytest -q' } },
        ]}
      />,
    );
    expect(screen.getByTestId('tool-group-block')).toHaveAttribute('data-status', 'running');
    expect(screen.getByTestId('tool-run-ticker')).toHaveTextContent('Running pytest -q');
  });

  it('renders every call inline when the preference asks for cards', () => {
    localStorage.setItem('niuu.compactUx.toolRuns', 'cards');
    render(
      <AssistantMessage
        message={assistant([
          { type: 'tool_use', id: 'r1', name: 'Read', input: { file_path: 'a.ts' } },
          { type: 'tool_use', id: 'r2', name: 'Bash', input: { command: 'ls' } },
        ])}
      />,
    );
    expect(screen.getAllByTestId('tool-block')).toHaveLength(2);
  });

  it('renders Task and Agent calls as subagent cards, not rows', () => {
    render(
      <AssistantMessage
        message={assistant([
          { type: 'tool_use', id: 'b', name: 'Bash', input: { command: 'ls' } },
          { type: 'tool_use', id: 't', name: 'Task', input: { description: 'Check gates' } },
          { type: 'tool_result', tool_use_id: 't', content: 'All good' },
        ])}
      />,
    );
    expect(screen.getByTestId('subagent-card')).toHaveTextContent('Check gates');
    expect(screen.getByTestId('subagent-status')).toHaveTextContent('Done');
    expect(screen.getByTestId('subagent-result')).toHaveTextContent('All good');
    expect(screen.getAllByTestId('tool-group-block')).toHaveLength(1);
  });

  it('shows a subagent without a result as running only while the message streams', () => {
    const parts: ChatMessagePart[] = [
      { type: 'tool_use', id: 't', name: 'Agent', input: { prompt: 'Look' } },
    ];
    const { rerender } = render(<StreamingMessage content="" parts={parts} />);
    expect(screen.getByTestId('subagent-status')).toHaveTextContent('Running');
    rerender(<AssistantMessage message={assistant(parts)} />);
    expect(screen.queryByTestId('subagent-status')).toBeNull();
  });
});

describe('compaction', () => {
  it('renders a compaction part inside a message as a centred rule', () => {
    render(
      <AssistantMessage
        message={assistant([
          { type: 'compaction' },
          { type: 'text', id: 't', text: 'Continuing.' },
        ])}
      />,
    );
    expect(screen.getByTestId('compaction-marker')).toHaveTextContent(/^Context compacted$/);
    expect(screen.getByText('Continuing.')).toBeInTheDocument();
  });

  it('renders a message made only of a compaction part as the rule alone, with figures if carried', () => {
    const part = { type: 'compaction', tokensBefore: 182000, tokensAfter: 41000 };
    render(<AssistantMessage message={assistant([part as unknown as ChatMessagePart])} />);
    expect(screen.queryByTestId('assistant-message')).toBeNull();
    expect(screen.getByTestId('compaction-marker')).toHaveTextContent(
      'Context compacted · 182k → 41k tokens',
    );
  });
});

describe('provider error', () => {
  it('renders an error system message as the error card', () => {
    render(
      <SystemMessage
        message={{
          id: 'e',
          role: 'system',
          content: 'HTTP 429 · rate_limit_exceeded · req_7f3a',
          createdAt: now,
          status: 'error',
          metadata: { messageType: 'error' },
        }}
      />,
    );
    expect(screen.getByTestId('provider-error-card')).toHaveTextContent(
      "The model couldn't run this turn",
    );
    expect(screen.getByTestId('provider-error-card')).toHaveTextContent('req_7f3a');
    expect(screen.queryByTestId('system-message')).toBeNull();
  });

  it('leaves ordinary system messages alone', () => {
    render(
      <SystemMessage
        message={{
          id: 's',
          role: 'system',
          content: 'Session started',
          createdAt: now,
          status: 'done',
        }}
      />,
    );
    expect(screen.getByTestId('system-message')).toBeInTheDocument();
    expect(screen.queryByTestId('provider-error-card')).toBeNull();
  });
});

describe('user message delivery', () => {
  it('shows a queued message as a dashed bubble with a pulsing note', () => {
    const { container } = render(<UserMessage message={user({ delivery: { state: 'queued' } })} />);
    expect(container.querySelector('.niuu-chat-user-bubble')).toHaveClass(
      'niuu-chat-user-bubble--queued',
    );
    expect(screen.getByRole('status')).toHaveTextContent(
      'Queued · waiting for the agent to take it',
    );
    expect(container.querySelector('.niuu-chat-delivery-pulse')).not.toBeNull();
  });

  it('shows an amber note with the broker sentence when delivery is unconfirmed', () => {
    const { container } = render(
      <UserMessage
        message={user({ delivery: { state: 'unconfirmed', error: 'No acknowledgement in time' } })}
      />,
    );
    expect(screen.getByRole('status')).toHaveTextContent(
      'Delivery not confirmed · No acknowledgement in time',
    );
    expect(container.querySelector('.niuu-chat-user-bubble--queued')).toBeNull();
  });

  it('shows a critical note with the broker sentence when delivery failed', () => {
    render(
      <UserMessage
        message={user({ delivery: { state: 'failed', error: 'Session is stopped' } })}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Not delivered · Session is stopped');
  });

  it.each([['active'], ['delivered'], [undefined]] as const)(
    'renders a normal bubble for %s',
    (state) => {
      const { container } = render(
        <UserMessage message={user(state ? { delivery: { state } } : {})} />,
      );
      expect(container.querySelector('.niuu-chat-user-bubble--queued')).toBeNull();
      expect(screen.queryByRole('status')).toBeNull();
      expect(screen.queryByRole('alert')).toBeNull();
      expect(screen.getByText('Also link NIU-1342 in the PR body.')).toBeInTheDocument();
    },
  );

  it('notes a failure without a sentence plainly', () => {
    render(<UserMessage message={user({ delivery: { state: 'failed' } })} />);
    expect(screen.getByRole('alert')).toHaveTextContent(/^Not delivered/);
    expect(screen.getByRole('alert').textContent).not.toContain('·');
  });
});
