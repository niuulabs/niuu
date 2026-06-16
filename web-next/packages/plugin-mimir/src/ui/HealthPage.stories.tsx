import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { HealthPage } from './HealthPage';
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

const meta: Meta<typeof HealthPage> = {
  title: 'Mímir/HealthPage',
  component: HealthPage,
  decorators: [withMimir()],
  parameters: { layout: 'fullscreen' },
};
export default meta;

type Story = StoryObj<typeof HealthPage>;

/** Doctor checklist stacked above the lint issue detail — one health surface. */
export const Default: Story = {};

/** Backend without the doctor subsystem still shows the lint detail. */
export const DoctorUnavailable: Story = {
  decorators: [
    withMimir({
      ...createMimirMockAdapter(),
      mounts: {
        ...createMimirMockAdapter().mounts,
        getDoctor: async () => null,
      },
    }),
  ],
};
