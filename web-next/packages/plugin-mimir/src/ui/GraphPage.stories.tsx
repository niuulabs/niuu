import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { GraphPage } from './GraphPage';
import { createMimirMockAdapter } from '../adapters/mock';
import type { IMimirService } from '../ports';

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function withMimir(service?: IMimirService) {
  const svc = service ?? createMimirMockAdapter();
  function MimirDecorator(Story: React.ComponentType) {
    return (
      <QueryClientProvider client={makeClient()}>
        <ServicesProvider services={{ mimir: svc }}>
          <Story />
        </ServicesProvider>
      </QueryClientProvider>
    );
  }
  return MimirDecorator;
}

const meta: Meta<typeof GraphPage> = {
  title: 'Mímir/GraphPage',
  component: GraphPage,
  decorators: [withMimir()],
  parameters: { layout: 'fullscreen' },
};
export default meta;

type Story = StoryObj<typeof GraphPage>;

export const Default: Story = {};

export const WithError: Story = {
  decorators: [
    withMimir({
      ...createMimirMockAdapter(),
      pages: {
        ...createMimirMockAdapter().pages,
        getGraph: async () => {
          throw new Error('Graph service unavailable');
        },
      },
    }),
  ],
};
