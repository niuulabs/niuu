import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { SessionsView } from './SessionsView';
import { createMockSessionStream, createMockRavenStream } from '../adapters/mock';

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

function withProviders(services: Record<string, unknown>) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={services}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

const meta: Meta<typeof SessionsView> = {
  title: 'Ravn/SessionsView',
  component: SessionsView,
};

export default meta;
type Story = StoryObj<typeof SessionsView>;

export const Default: Story = {
  decorators: [
    (Story) => {
      const Wrapper = withProviders({
        'ravn.sessions': createMockSessionStream(),
        'ravn.ravens': createMockRavenStream(),
      });
      return (
        <Wrapper>
          <Story />
        </Wrapper>
      );
    },
  ],
};

export const ErrorState: Story = {
  decorators: [
    (Story) => {
      const Wrapper = withProviders({
        'ravn.sessions': {
          listSessions: async () => {
            throw new Error('service unavailable');
          },
        },
        'ravn.ravens': createMockRavenStream(),
      });
      return (
        <Wrapper>
          <Story />
        </Wrapper>
      );
    },
  ],
};
