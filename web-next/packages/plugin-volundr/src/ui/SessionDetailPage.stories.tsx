import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { SessionDetailPage } from './SessionDetailPage';
import {
  createMockPtyStream,
  createMockFileSystemPort,
  createMockSessionStore,
  createMockMetricsStream,
} from '../adapters/mock';

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

function withProviders(story: React.ReactNode) {
  return (
    <QueryClientProvider client={queryClient}>
      <ServicesProvider
        services={{
          ptyStream: createMockPtyStream(),
          filesystem: createMockFileSystemPort(),
          sessionStore: createMockSessionStore(),
          metricsStream: createMockMetricsStream(),
        }}
      >
        <div style={{ height: '600px', display: 'flex', flexDirection: 'column' }}>{story}</div>
      </ServicesProvider>
    </QueryClientProvider>
  );
}

const meta: Meta<typeof SessionDetailPage> = {
  title: 'Völundr/SessionDetailPage',
  component: SessionDetailPage,
  decorators: [(Story) => withProviders(<Story />)],
  args: {
    sessionId: 'ds-1',
    readOnly: false,
  },
};

export default meta;
type Story = StoryObj<typeof SessionDetailPage>;

/** Overview tab — session metadata and resource bars. */
export const OverviewTab: Story = {
  args: { initialTab: 'overview' },
};

/** Terminal tab — xterm.js interactive shell. */
export const TerminalTab: Story = {
  args: { initialTab: 'terminal' },
};

/** Files tab — file tree and viewer. */
export const FilesTab: Story = {
  args: { initialTab: 'files' },
};

/** Exec tab — run-and-wait commands with history. */
export const ExecTab: Story = {
  args: { initialTab: 'exec' },
};

/** Events tab — session lifecycle events timeline. */
export const EventsTab: Story = {
  args: { initialTab: 'events' },
};

/** Metrics tab — live sparkline charts. */
export const MetricsTab: Story = {
  args: { initialTab: 'metrics' },
};

/** Read-only (archived) session with all tabs. */
export const ArchivedSession: Story = {
  args: { sessionId: 'ds-5', readOnly: true, initialTab: 'overview' },
};
