import type { Meta, StoryObj } from '@storybook/react';
import { MessageRow } from './MessageRow';
import type { Message } from '../domain/message';

function msg(overrides: Partial<Message>): Message {
  return {
    id: '00000000-0000-4000-8000-000000000001',
    sessionId: 'sess-1',
    kind: 'user',
    content: 'hello',
    ts: '2026-04-15T09:12:35Z',
    ...overrides,
  };
}

const meta: Meta<typeof MessageRow> = {
  title: 'Ravn/MessageRow',
  component: MessageRow,
};

export default meta;
type Story = StoryObj<typeof MessageRow>;

export const User: Story = {
  args: { message: msg({ kind: 'user', content: 'Please implement the login form' }) },
};

export const Assistant: Story = {
  args: {
    message: msg({
      kind: 'asst',
      content: "I'll create the login form at `src/auth/LoginForm.tsx`.",
    }),
  },
};

export const System: Story = {
  args: {
    message: msg({
      kind: 'system',
      content: '# coding-agent\nYou are a senior software engineer.',
    }),
  },
};

export const ToolCall: Story = {
  args: {
    message: msg({
      kind: 'tool_call',
      toolName: 'file.read',
      content: JSON.stringify({ path: 'src/auth/LoginForm.tsx' }, null, 2),
    }),
  },
};

export const ToolResult: Story = {
  args: {
    message: msg({
      kind: 'tool_result',
      toolName: 'file.read',
      content: JSON.stringify(
        { content: '// LoginForm.tsx\nexport function LoginForm() {}' },
        null,
        2,
      ),
    }),
  },
};

export const Emit: Story = {
  args: {
    message: msg({
      kind: 'emit',
      content: JSON.stringify({
        event: 'code.changed',
        payload: { file: 'src/auth/LoginForm.tsx' },
      }),
    }),
  },
};

export const Think: Story = {
  args: {
    message: msg({
      kind: 'think',
      content:
        'I need to check the existing auth setup first. Let me read the directory structure.',
    }),
  },
};

/** Gallery of all message kinds */
export const Gallery: Story = {
  render: () => (
    <div className="rv-story-wrapper">
      <MessageRow message={msg({ kind: 'user', content: 'Please implement the login form' })} />
      <MessageRow
        message={msg({
          kind: 'asst',
          content: "I'll create the login form at `src/auth/LoginForm.tsx`.",
        })}
      />
      <MessageRow
        message={msg({ kind: 'system', content: '# System: coding-agent persona active.' })}
      />
      <MessageRow
        message={msg({
          kind: 'tool_call',
          toolName: 'file.read',
          content: '{"path":"src/auth/LoginForm.tsx"}',
        })}
      />
      <MessageRow
        message={msg({
          kind: 'tool_result',
          toolName: 'file.read',
          content: '{"content":"// file not found"}',
        })}
      />
      <MessageRow
        message={msg({
          kind: 'emit',
          content: '{"event":"code.changed","payload":{"file":"src/auth/LoginForm.tsx"}}',
        })}
      />
      <MessageRow
        message={msg({ kind: 'think', content: 'I need to check the existing auth setup first.' })}
      />
    </div>
  ),
};
