import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { LogView } from './LogView';
import { createMockSessionStream, createMockRavenStream } from '../adapters/mock';

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

const meta: Meta<typeof LogView> = {
  title: 'Ravn/LogView',
  component: LogView,
};

export default meta;
type Story = StoryObj<typeof LogView>;

export const Default: Story = {
  decorators: [
    (Story) => (
      <QueryClientProvider client={client}>
        <ServicesProvider
          services={{
            'ravn.sessions': createMockSessionStream(),
            'ravn.ravens': createMockRavenStream(),
          }}
        >
          <div className="rv-story-wrapper--tall">
            <Story />
          </div>
        </ServicesProvider>
      </QueryClientProvider>
    ),
  ],
};
