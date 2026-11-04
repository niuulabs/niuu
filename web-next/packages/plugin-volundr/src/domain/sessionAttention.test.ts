import { describe, expect, it } from 'vitest';
import type { ChatMessage, RoomParticipant } from '@niuulabs/ui';
import { buildAttentionItems, latestChecklist, runningPeers } from './sessionAttention';

function todoMessage(id: string, todos: unknown): ChatMessage {
  return {
    id,
    role: 'assistant',
    content: '',
    createdAt: new Date(0),
    parts: [{ type: 'tool_use', name: 'TodoWrite', input: { todos } }],
  };
}

describe('buildAttentionItems', () => {
  it('lists gates, then permissions, then questions', () => {
    const items = buildAttentionItems({
      gates: [{ id: 'gate-1', title: 'Plan review', detail: 'Approve the plan' }],
      permissions: [
        { requestId: 'perm-1', toolName: 'Bash', description: 'Run a command', command: 'ls' },
        { requestId: 'perm-2', toolName: 'Write', description: 'Write a file' },
      ],
      inputRequests: [
        { requestId: 'input-1', questions: [{ prompt: 'Which branch?', choices: [] }] },
      ],
    });

    expect(items.map((item) => [item.kind, item.title, item.detail])).toEqual([
      ['gate', 'Plan review', 'Approve the plan'],
      ['permission', 'Bash', 'ls'],
      ['permission', 'Write', 'Write a file'],
      ['input', 'Which branch?', 'Answer in chat.'],
    ]);
  });

  it('points a question nothing here can answer at the terminal', () => {
    const [item] = buildAttentionItems({
      gates: [],
      permissions: [],
      inputRequests: [{ requestId: 'input-1', questions: [], answerable: false }],
    });

    expect(item).toMatchObject({
      title: 'The agent has a question',
      detail: 'Answer this in the session terminal.',
    });
  });
});

describe('latestChecklist', () => {
  it('uses only the newest task list', () => {
    const checklist = latestChecklist([
      todoMessage('old', [{ content: 'Stale', status: 'pending' }]),
      { id: 'text', role: 'assistant', content: 'Working', createdAt: new Date(0) },
      todoMessage('new', [
        { content: 'Read the code', status: 'completed' },
        { content: 'Write the fix', status: 'in_progress' },
        { content: 'Run tests', status: 'pending' },
      ]),
    ]);

    expect(checklist).toEqual([
      { label: 'Read the code', status: 'completed' },
      { label: 'Write the fix', status: 'in_progress' },
      { label: 'Run tests', status: 'pending' },
    ]);
  });

  it('skips malformed entries and treats an unknown status as pending', () => {
    const checklist = latestChecklist([
      todoMessage('todo', [null, { status: 'pending' }, { content: 'Ship', status: 'later' }]),
    ]);

    expect(checklist).toEqual([{ label: 'Ship', status: 'pending' }]);
  });

  it('returns nothing without a task list', () => {
    expect(latestChecklist([])).toEqual([]);
    expect(latestChecklist([todoMessage('bad', 'not a list')])).toEqual([]);
  });
});

describe('runningPeers', () => {
  it('keeps agents and leaves out the session gateway', () => {
    const agent: RoomParticipant = { peerId: 'a', persona: 'coder', participantType: 'ravn' };
    const gateway: RoomParticipant = { peerId: 'b', persona: 'skuld', participantType: 'skuld' };

    expect(
      runningPeers(
        new Map([
          ['a', agent],
          ['b', gateway],
        ]),
      ),
    ).toEqual([agent]);
  });
});
