import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AssistantMessage, StreamingMessage, messageRenderKey } from './ChatMessages';
import { SessionChat } from '../SessionChat/SessionChat';
import type { ChatMessage, ChatMessagePart } from '../../types';

const tools: ChatMessagePart[] = [
  { type: 'tool_use', id: 'command-a', name: 'Bash', input: { command: 'printf observed' } },
  { type: 'tool_result', tool_use_id: 'command-a', content: 'observed' },
];
const before = 'First commentary.';
const after = 'Final café 東京 answer.';
const message = (parts: ChatMessagePart[]): ChatMessage => ({
  id: 'assistant-turn',
  role: 'assistant',
  createdAt: new Date('2026-09-08T00:00:00Z'),
  content: `${before}\n\n${after}`,
  parts,
  status: 'done',
});

describe('public text and tool chronology', () => {
  it('preserves nonempty aggregate prose once for legacy tools-only history', () => {
    render(<AssistantMessage message={message(tools)} />);
    expect(screen.getByText(before)).toBeInTheDocument();
    expect(screen.getByText(after)).toBeInTheDocument();
    expect(screen.getAllByText(before)).toHaveLength(1);
    expect(screen.getByTestId('tool-block')).toBeInTheDocument();
  });

  it('preserves the same legacy prose while streaming', () => {
    render(<StreamingMessage content={`${before}\n\n${after}`} parts={tools} />);
    expect(screen.getByText(before)).toBeInTheDocument();
    expect(screen.getByText(after)).toBeInTheDocument();
  });

  it('preserves structured message boundaries, phases and stable DOM anchors after refresh', () => {
    const parts: ChatMessagePart[] = [
      { type: 'text', id: 'commentary-a', phase: 'commentary', text: before },
      ...tools,
      { type: 'text', id: 'answer-a', phase: 'final_answer', text: after },
    ];
    const { container, rerender } = render(<AssistantMessage message={message(parts)} />);
    const commentary = container.querySelector('[data-text-id="commentary-a"]');
    const answer = container.querySelector('[data-text-id="answer-a"]');
    const command = screen.getByTestId('tool-block');
    expect(commentary).not.toBeNull();
    expect(answer).toHaveAttribute('data-text-phase', 'final_answer');
    expect(
      commentary!.compareDocumentPosition(command) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      command.compareDocumentPosition(answer!) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    rerender(<AssistantMessage message={message(parts.map((part) => ({ ...part })))} />);
    expect(container.querySelector('[data-text-id="commentary-a"]')).toBe(commentary);
    expect(container.querySelector('[data-text-id="answer-a"]')).toBe(answer);
    expect(screen.getByTestId('tool-block')).toBe(command);
    expect(screen.getAllByText(before)).toHaveLength(1);
    expect(screen.getAllByText(after)).toHaveLength(1);
  });

  it('does not let an empty text start suppress legacy fallback', () => {
    render(<AssistantMessage message={message([{ type: 'text', text: '' }, ...tools])} />);
    expect(screen.getByText(before)).toBeInTheDocument();
    expect(screen.getByText(after)).toBeInTheDocument();
  });
});

it('keeps native prose anchors through a running-to-settled message update', () => {
  const parts: ChatMessagePart[] = [
    { type: 'text', id: 'a', text: before, phase: 'commentary', complete: true },
    ...tools,
    { type: 'text', id: 'b', text: after, phase: 'final_answer', complete: false },
  ];
  const { container, rerender } = render(
    <AssistantMessage message={{ ...message(parts), status: 'running' }} />,
  );
  const anchor = container.querySelector('[data-text-id="a"]');
  rerender(
    <AssistantMessage message={message(parts.map((part) => ({ ...part, complete: true })))} />,
  );
  expect(container.querySelector('[data-text-id="a"]')).toBe(anchor);
});

it('the actual chat list retains native row state when its running turn settles', () => {
  const parts: ChatMessagePart[] = [
    { type: 'text', id: 'a', text: before, phase: 'commentary', turn_id: 't' },
    ...tools,
    { type: 'text', id: 'b', text: after, phase: 'final_answer', turn_id: 't' },
  ];
  const pending = { ...message(parts), status: 'running' as const };
  const { container, rerender } = render(<SessionChat messages={[pending]} onSend={() => {}} />);
  const anchor = container.querySelector('[data-text-id="a"]');
  const tool = container.querySelector('[data-testid="tool-block"]');
  expect(anchor).not.toBeNull();
  rerender(
    <SessionChat
      messages={[{ ...message(parts), id: 'canonical-from-snapshot' }]}
      onSend={() => {}}
    />,
  );
  expect(container.querySelector('[data-text-id="a"]')).toBe(anchor);
  expect(container.querySelector('[data-testid="tool-block"]')).toBe(tool);
});

it('adjacent text-only native items retain their own phase and anchor without tools', () => {
  const { container } = render(
    <AssistantMessage
      message={message([
        { type: 'text', id: 'a', text: before, phase: 'commentary' },
        { type: 'text', id: 'b', text: after, phase: 'final_answer' },
      ])}
    />,
  );
  expect(container.querySelector('[data-text-id="a"]')).toHaveAttribute(
    'data-text-phase',
    'commentary',
  );
  expect(container.querySelector('[data-text-id="b"]')).toHaveAttribute(
    'data-text-phase',
    'final_answer',
  );
  expect(container.textContent?.match(/First commentary/g)).toHaveLength(1);
});

it('native steering fragments do not collide and a tools-first anchor survives later text', () => {
  const first = {
    type: 'tool_use' as const,
    id: 'first-tool',
    name: 'Bash',
    turn_id: 'same-turn',
    thread_id: 'same-thread',
  };
  const before = message([first]);
  const after = message([
    first,
    { type: 'text', id: 'a', text: 'Later', turn_id: 'same-turn', thread_id: 'same-thread' },
  ]);
  expect(messageRenderKey(before)).toBe(messageRenderKey(after));
  expect(messageRenderKey(after)).not.toBe(
    messageRenderKey(
      message([
        {
          type: 'text',
          id: 'other-fragment',
          text: 'Steered',
          turn_id: 'same-turn',
          thread_id: 'same-thread',
        },
      ]),
    ),
  );
});
