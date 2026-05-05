import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { ClustersPage } from './ClustersPage';
import { createMockClusterAdapter } from '../adapters/mock';
import type { IClusterAdapter } from '../ports/IClusterAdapter';

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function withVolundr(adapter?: IClusterAdapter) {
  const svc = adapter ?? createMockClusterAdapter();
  const Decorator = (Story: React.ComponentType) => (
    <QueryClientProvider client={makeClient()}>
      <ServicesProvider services={{ 'volundr.clusters': svc }}>
        <Story />
      </ServicesProvider>
    </QueryClientProvider>
  );
  return Decorator;
}

const meta: Meta<typeof ClustersPage> = {
  title: 'Völundr/ClustersPage',
  component: ClustersPage,
  decorators: [withVolundr()],
  parameters: { layout: 'fullscreen' },
};
export default meta;

type Story = StoryObj<typeof ClustersPage>;

export const Default: Story = {};

export const Empty: Story = {
  decorators: [
    withVolundr({
      ...createMockClusterAdapter(),
      getClusters: async () => [],
    }),
  ],
};

export const LoadError: Story = {
  decorators: [
    withVolundr({
      ...createMockClusterAdapter(),
      getClusters: async () => {
        throw new Error('Cluster service unavailable');
      },
    }),
  ],
};
