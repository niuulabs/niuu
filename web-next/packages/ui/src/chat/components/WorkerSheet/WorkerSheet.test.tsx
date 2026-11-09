import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { WorkerSheet } from './WorkerSheet';
import type { AgentInternalEvent, ChatMessage, RoomParticipant } from '../../types';
import type { Worker, WorkerOutcome } from '../../workers';

const participant: RoomParticipant = {
  peerId: 'coder',
  persona: 'coder',
  participantType: 'ravn',
};

const messageOutcome: WorkerOutcome = {
  peerId: 'coder',
  persona: 'coder',
  eventType: 'code.changed',
  verdict: 'pass',
  summary: 'Probes implemented.',
  fields: {},
  at: new Date('2026-11-05T05:19:00Z'),
  tone: 'pass',
  messageId: 'm2',
};

const liveOutcome: WorkerOutcome = {
  peerId: 'coder',
  persona: 'coder',
  eventType: 'code.fixed',
  verdict: 'needs_changes',
  summary: 'Fixed the freshness probe, one test left.',
  fields: { tests: 6 },
  at: new Date('2026-11-05T05:27:00Z'),
  tone: 'changes',
};

const worker: Worker = {
  peerId: 'coder',
  name: 'coder',
  persona: 'coder',
  color: '#aa5500',
  status: 'working',
  consumes: ['code.requested', 'review.changes_requested'],
  emits: ['code.changed'],
  outcomes: [messageOutcome, liveOutcome],
  latest: liveOutcome,
};

const message = (id: string, peerId: string, content: string, minute: number): ChatMessage => ({
  id,
  role: 'assistant',
  content,
  status: 'done',
  createdAt: new Date(Date.UTC(2026, 10, 5, 5, minute)),
  participant: { peerId, persona: peerId },
});

const messages: ChatMessage[] = [
  message('m1', 'coder', 'Reading the feed header.', 17),
  message('m2', 'coder', '```outcome\nverdict: pass\nsummary: Probes implemented.\n```', 19),
  message('m3', 'reviewer', 'The stale-cache probe is wrong.', 22),
  {
    id: 'u1',
    role: 'user',
    content: 'Build the probe.',
    createdAt: new Date('2026-11-05T05:12:00Z'),
  },
];

const events: AgentInternalEvent[] = [
  { id: 'e1', frameType: 'thought', data: 'The review is right.' },
  {
    id: 'e2',
    frameType: 'tool_start',
    data: 'edit',
    metadata: { tool_name: 'Edit', input: { path: 'probes/freshness.py' } },
  },
  { id: 'e3', frameType: 'tool_result', data: 'edited 1 file', metadata: { tool_name: 'Edit' } },
  { id: 'e4', frameType: 'text', data: 'Running the probe tests now.' },
];

const renderSheet = (props: Partial<React.ComponentProps<typeof WorkerSheet>> = {}) =>
  render(
    <WorkerSheet
      worker={worker}
      participant={participant}
      messages={messages}
      events={events}
      onClose={vi.fn()}
      onSendDirected={vi.fn()}
      {...props}
    />,
  );

describe('WorkerSheet', () => {
  it('shows the worker, its status and its latest verdict', () => {
    renderSheet();
    const sheet = screen.getByTestId('worker-sheet');
    expect(within(sheet).getByRole('heading', { name: 'coder' })).toBeInTheDocument();
    expect(within(sheet).getByText('Working · worker')).toBeInTheDocument();
    const head = sheet.querySelector('header') as HTMLElement;
    expect(within(head).getByText('Changes requested')).toHaveAttribute('data-tone', 'changes');
  });

  it('lists what the worker listens for and what it emits', () => {
    renderSheet();
    const sheet = screen.getByTestId('worker-sheet');
    expect(within(sheet).getByText('Listens for')).toBeInTheDocument();
    expect(within(sheet).getByText('code.requested')).toBeInTheDocument();
    expect(within(sheet).getByText('review.changes_requested')).toBeInTheDocument();
    expect(within(sheet).getByText('Emits')).toBeInTheDocument();
    expect(within(sheet).getByText('code.changed')).toBeInTheDocument();
  });

  it('leaves the event lists to a host that shows them elsewhere', () => {
    renderSheet({ showEvents: false });
    expect(screen.queryByText('Listens for')).toBeNull();
    expect(screen.queryByText('Emits')).toBeNull();
  });

  it('leaves the event lists out for a worker that has none', () => {
    renderSheet({ worker: { ...worker, consumes: [], emits: [] } });
    expect(screen.queryByText('Listens for')).toBeNull();
    expect(screen.queryByText('Emits')).toBeNull();
  });

  it('keeps only this worker messages', () => {
    renderSheet();
    expect(screen.getByText('Reading the feed header.')).toBeInTheDocument();
    expect(screen.queryByText('The stale-cache probe is wrong.')).toBeNull();
    expect(screen.queryByText('Build the probe.')).toBeNull();
    expect(screen.getAllByTestId('sheet-message')).toHaveLength(2);
  });

  it('shows an outcome once: inside its message, or alone when it was a live event', () => {
    renderSheet();
    const cards = screen.getAllByTestId('outcome-card');
    expect(cards).toHaveLength(2);
    expect(screen.getAllByText('Probes implemented.')).toHaveLength(1);
    expect(screen.getAllByText('Fixed the freshness probe, one test left.')).toHaveLength(1);
    expect(cards[0]).toHaveTextContent('coder');
  });

  it('shows the agent events: thoughts, tool calls, results and text', () => {
    renderSheet();
    expect(screen.getByText('The review is right.')).toHaveAttribute('data-frame', 'thought');
    expect(screen.getByText('Running')).toBeInTheDocument();
    expect(screen.getByText('Result')).toBeInTheDocument();
    expect(screen.getByText(/probes\/freshness\.py/)).toBeInTheDocument();
    expect(screen.getByText('edited 1 file')).toBeInTheDocument();
    expect(screen.getByText('Running the probe tests now.')).toBeInTheDocument();
  });

  it('shows a tool call with no input as a plain row', () => {
    renderSheet({ events: [{ id: 'e5', frameType: 'tool_start', data: 'ls' }] });
    expect(screen.getByText('Running').closest('details')).toBeNull();
  });

  it('says so when there is nothing from the worker yet', () => {
    renderSheet({
      messages: [],
      events: [],
      worker: { ...worker, outcomes: [], latest: undefined },
    });
    expect(screen.getByText('Nothing from coder yet.')).toBeInTheDocument();
    expect(screen.queryByText('Changes requested')).toBeNull();
  });

  it('messages the worker directly', () => {
    const onSendDirected = vi.fn();
    renderSheet({ onSendDirected });
    const input = screen.getByPlaceholderText('Message coder directly…');
    fireEvent.change(input, { target: { value: '  use the header timestamp  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send to coder' }));
    expect(onSendDirected).toHaveBeenCalledWith([participant], 'use the header timestamp', []);
    expect(input).toHaveValue('');
  });

  it('does not send an empty message', () => {
    const onSendDirected = vi.fn();
    renderSheet({ onSendDirected });
    fireEvent.change(screen.getByPlaceholderText('Message coder directly…'), {
      target: { value: '   ' },
    });
    expect(screen.getByRole('button', { name: 'Send to coder' })).toBeDisabled();
    fireEvent.submit(screen.getByPlaceholderText('Message coder directly…'));
    expect(onSendDirected).not.toHaveBeenCalled();
  });

  it('has no composer when the host cannot send directly', () => {
    renderSheet({ onSendDirected: undefined });
    expect(screen.queryByPlaceholderText('Message coder directly…')).toBeNull();
  });

  it('keeps following new activity only while the reader is near the end', () => {
    renderSheet();
    const body = document.querySelector('.niuu-chat-sheet-body') as HTMLElement;
    Object.defineProperty(body, 'scrollHeight', { configurable: true, value: 1000 });
    Object.defineProperty(body, 'clientHeight', { configurable: true, value: 300 });
    body.scrollTop = 0;
    fireEvent.scroll(body);
    body.scrollTop = 650;
    fireEvent.scroll(body);
    expect(body.scrollTop).toBe(650);
  });

  it('goes back to the coordinator with its back button', () => {
    const onClose = vi.fn();
    renderSheet({ onClose });
    fireEvent.click(screen.getByRole('button', { name: 'Back to the coordinator' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('puts timed activity where it happened among the messages', () => {
    renderSheet({
      messages: [
        {
          id: 'late',
          role: 'assistant',
          content: 'Second reply.',
          createdAt: new Date(Date.UTC(2026, 10, 5, 5, 30)),
          participant: { peerId: worker.peerId, persona: 'coder' },
        },
      ],
      events: [
        {
          id: 'early',
          frameType: 'thought',
          data: 'Thinking first.',
          timestamp: new Date(Date.UTC(2026, 10, 5, 5, 10)),
        },
        { id: 'untimed', frameType: 'thought', data: 'No time given.' },
      ],
    });
    const body = screen.getByTestId('worker-sheet').querySelector('.niuu-chat-sheet-body')!;
    const text = body.textContent ?? '';
    expect(text.indexOf('Thinking first.')).toBeLessThan(text.indexOf('Second reply.'));
    expect(text.indexOf('Second reply.')).toBeLessThan(text.indexOf('No time given.'));
  });

  it('closes on Escape, including from its own composer', () => {
    const onClose = vi.fn();
    renderSheet({ onClose });
    fireEvent.keyDown(document.body, { key: 'Escape' });
    fireEvent.keyDown(screen.getByPlaceholderText('Message coder directly…'), { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('leaves Escape in a field outside the sheet to that field', () => {
    const onClose = vi.fn();
    renderSheet({ onClose });
    const outside = document.createElement('textarea');
    document.body.appendChild(outside);
    fireEvent.keyDown(outside, { key: 'Escape' });
    outside.remove();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('ignores other keys', () => {
    const onClose = vi.fn();
    renderSheet({ onClose });
    fireEvent.keyDown(document.body, { key: 'Enter' });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('stops listening once closed', () => {
    const onClose = vi.fn();
    const { unmount } = renderSheet({ onClose });
    unmount();
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
  });
});
