import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { HistoryPage } from './HistoryPage';
import { createMockSessionStore } from '../adapters/mock';
import type { ISessionStore } from '../ports/ISessionStore';

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function withVolundr(store?: ISessionStore) {
  const svc = store ?? createMockSessionStore();
  const Decorator = (Story: React.ComponentType) => (
    <QueryClientProvider client={makeClient()}>
      <ServicesProvider services={{ 'volundr.sessions': svc }}>
        <Story />
      </ServicesProvider>
    </QueryClientProvider>
  );
  return Decorator;
}

const meta: Meta<typeof HistoryPage> = {
  title: 'Völundr/HistoryPage',
  component: HistoryPage,
  decorators: [withVolundr()],
  parameters: { layout: 'fullscreen' },
};
export default meta;

type Story = StoryObj<typeof HistoryPage>;

export const Default: Story = {};

export const Empty: Story = {
  decorators: [
    withVolundr({
      ...createMockSessionStore(),
      listSessions: async () => [],
    }),
  ],
};

export const LoadError: Story = {
  decorators: [
    withVolundr({
      ...createMockSessionStore(),
      listSessions: async () => {
        throw new Error('Session store unavailable');
      },
    }),
  ],
};
