import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { TriggersView } from './TriggersView';
import { createMockTriggerStore } from '../adapters/mock';

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

const meta: Meta<typeof TriggersView> = {
  title: 'Ravn/TriggersView',
  component: TriggersView,
};

export default meta;
type Story = StoryObj<typeof TriggersView>;

export const Default: Story = {
  decorators: [
    (Story) => (
      <QueryClientProvider client={client}>
        <ServicesProvider services={{ 'ravn.triggers': createMockTriggerStore() }}>
          <Story />
        </ServicesProvider>
      </QueryClientProvider>
    ),
  ],
};
