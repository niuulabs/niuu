import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { AnalyticsPage } from './AnalyticsPage';
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

const meta: Meta<typeof AnalyticsPage> = {
  title: 'Mímir/AnalyticsPage',
  component: AnalyticsPage,
  decorators: [withMimir()],
  parameters: { layout: 'fullscreen' },
};
export default meta;

type Story = StoryObj<typeof AnalyticsPage>;

export const Default: Story = {};

/** Backend without the eval subsystem — both sections show empty states. */
export const Empty: Story = {
  decorators: [
    withMimir({
      ...createMimirMockAdapter(),
      mounts: {
        ...createMimirMockAdapter().mounts,
        getEvalReport: async () => null,
        getQueryStats: async () => null,
      },
    }),
  ],
};

export const WithError: Story = {
  decorators: [
    withMimir({
      ...createMimirMockAdapter(),
      mounts: {
        ...createMimirMockAdapter().mounts,
        getEvalReport: async () => {
          throw new Error('Eval service unavailable');
        },
        getQueryStats: async () => {
          throw new Error('Query stats unavailable');
        },
      },
    }),
  ],
};
