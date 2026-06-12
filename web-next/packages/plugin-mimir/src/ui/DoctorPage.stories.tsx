import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { DoctorPage } from './DoctorPage';
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

const meta: Meta<typeof DoctorPage> = {
  title: 'Mímir/DoctorPage',
  component: DoctorPage,
  decorators: [withMimir()],
  parameters: { layout: 'fullscreen' },
};
export default meta;

type Story = StoryObj<typeof DoctorPage>;

export const Default: Story = {};

/** All checks green — no Run fixes button. */
export const AllPassing: Story = {
  decorators: [
    withMimir({
      ...createMimirMockAdapter(),
      mounts: {
        ...createMimirMockAdapter().mounts,
        getDoctor: async () => ({
          score: '3/3',
          worst: 'pass',
          checks: [
            {
              id: 'index-fresh',
              title: 'Mount index freshness',
              status: 'pass',
              detail: 'All mount indexes rebuilt within the last 24h',
              remediation: '',
              fixable: false,
            },
            {
              id: 'fts-index',
              title: 'Full-text index',
              status: 'pass',
              detail: 'FTS index consistent with page store',
              remediation: '',
              fixable: false,
            },
            {
              id: 'registry-health',
              title: 'Registry mount health',
              status: 'pass',
              detail: 'All registered mounts reachable',
              remediation: '',
              fixable: false,
            },
          ],
        }),
      },
    }),
  ],
};

/** Backend without the doctor subsystem. */
export const Empty: Story = {
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

export const WithError: Story = {
  decorators: [
    withMimir({
      ...createMimirMockAdapter(),
      mounts: {
        ...createMimirMockAdapter().mounts,
        getDoctor: async () => {
          throw new Error('Doctor service unavailable');
        },
      },
    }),
  ],
};
