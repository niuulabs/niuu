import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { DreamsPage } from './DreamsPage';
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

const meta: Meta<typeof DreamsPage> = {
  title: 'Mímir/DreamsPage',
  component: DreamsPage,
  decorators: [withMimir()],
  parameters: { layout: 'fullscreen' },
};
export default meta;

type Story = StoryObj<typeof DreamsPage>;

export const Default: Story = {};

export const Empty: Story = {
  decorators: [
    withMimir({
      ...createMimirMockAdapter(),
      lint: {
        ...createMimirMockAdapter().lint,
        getDreamCycles: async () => [],
      },
    }),
  ],
};

export const WithError: Story = {
  decorators: [
    withMimir({
      ...createMimirMockAdapter(),
      lint: {
        ...createMimirMockAdapter().lint,
        getDreamCycles: async () => {
          throw new Error('Dream service unavailable');
        },
      },
    }),
  ],
};
