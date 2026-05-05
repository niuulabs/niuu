import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { BudgetView } from './BudgetView';
import { createMockBudgetStream, createMockRavenStream } from '../adapters/mock';

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

const meta: Meta<typeof BudgetView> = {
  title: 'Ravn/BudgetView',
  component: BudgetView,
};

export default meta;
type Story = StoryObj<typeof BudgetView>;

export const Default: Story = {
  decorators: [
    (Story) => (
      <QueryClientProvider client={client}>
        <ServicesProvider
          services={{
            'ravn.budget': createMockBudgetStream(),
            'ravn.ravens': createMockRavenStream(),
          }}
        >
          <Story />
        </ServicesProvider>
      </QueryClientProvider>
    ),
  ],
};
