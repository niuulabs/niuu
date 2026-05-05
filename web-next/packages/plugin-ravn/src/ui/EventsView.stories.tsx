import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { EventsView } from './EventsView';
import { createMockPersonaStore } from '../adapters/mock';

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

const meta: Meta<typeof EventsView> = {
  title: 'Ravn/EventsView',
  component: EventsView,
};

export default meta;
type Story = StoryObj<typeof EventsView>;

export const Default: Story = {
  decorators: [
    (Story) => (
      <QueryClientProvider client={client}>
        <ServicesProvider services={{ 'ravn.personas': createMockPersonaStore() }}>
          <Story />
        </ServicesProvider>
      </QueryClientProvider>
    ),
  ],
};
