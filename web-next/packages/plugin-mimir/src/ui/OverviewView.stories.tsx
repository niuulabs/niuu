import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { OverviewView } from './OverviewView';
import { createMimirMockAdapter } from '../adapters/mock';

function withProviders(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <ServicesProvider services={{ mimir: createMimirMockAdapter() }}>{ui}</ServicesProvider>
    </QueryClientProvider>
  );
}

const meta = {
  title: 'Mimir/OverviewView',
  component: OverviewView,
  decorators: [(Story) => withProviders(<Story />)],
} satisfies Meta<typeof OverviewView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};
