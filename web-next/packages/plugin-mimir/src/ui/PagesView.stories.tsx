import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { PagesView } from './PagesView';
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
  title: 'Mimir/PagesView',
  component: PagesView,
  decorators: [(Story) => withProviders(<Story />)],
  parameters: {
    layout: 'fullscreen',
  },
} satisfies Meta<typeof PagesView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};
