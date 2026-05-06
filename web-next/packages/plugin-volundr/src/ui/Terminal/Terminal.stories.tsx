import type { Meta, StoryObj } from '@storybook/react';
import { Terminal } from './Terminal';
import type { IPtyStream } from '../../ports/IPtyStream';

function buildMockStream(opts?: { echo?: boolean; delay?: number }): IPtyStream {
  const subscribers = new Map<string, Array<(chunk: string) => void>>();

  function notify(sessionId: string, chunk: string) {
    for (const cb of subscribers.get(sessionId) ?? []) cb(chunk);
  }

  return {
    subscribe: (sessionId, onData) => {
      const existing = subscribers.get(sessionId) ?? [];
      existing.push(onData);
      subscribers.set(sessionId, existing);

      const delay = opts?.delay ?? 0;
      setTimeout(() => {
        notify(sessionId, '\x1b[1;32m[mock terminal]\x1b[0m connected\r\n$ ');
      }, delay);

      return () => {
        const updated = (subscribers.get(sessionId) ?? []).filter((cb) => cb !== onData);
        subscribers.set(sessionId, updated);
      };
    },
    send: (sessionId, data) => {
      if (!opts?.echo) return;
      // Echo the input back so typing produces visible output.
      notify(sessionId, data === '\r' ? '\r\n$ ' : data);
    },
  };
}

const meta: Meta<typeof Terminal> = {
  title: 'Plugins / Völundr / Terminal',
  component: Terminal,
  parameters: {
    layout: 'padded',
  },
  decorators: [
    (Story) => (
      <div style={{ height: 400, width: 700 }}>
        <Story />
      </div>
    ),
  ],
};
export default meta;

type Story = StoryObj<typeof Terminal>;

/** Interactive terminal connected to a mock echo stream. */
export const Live: Story = {
  args: {
    sessionId: 'sess-story-live',
    stream: buildMockStream({ echo: true }),
    readOnly: false,
  },
};

/** Read-only view for an archived session — input is disabled. */
export const ArchivedReadOnly: Story = {
  args: {
    sessionId: 'sess-story-archived',
    stream: buildMockStream({ echo: false }),
    readOnly: true,
  },
};

/** Terminal with a slow connection — shows "connecting…" indicator. */
export const SlowConnect: Story = {
  args: {
    sessionId: 'sess-story-slow',
    stream: buildMockStream({ delay: 4_000 }),
    readOnly: false,
  },
};
