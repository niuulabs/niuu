import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { LintPage } from './LintPage';
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

const meta: Meta<typeof LintPage> = {
  title: 'Mímir/LintPage',
  component: LintPage,
  decorators: [withMimir()],
  parameters: { layout: 'fullscreen' },
};
export default meta;

type Story = StoryObj<typeof LintPage>;

export const Default: Story = {};

export const Clean: Story = {
  decorators: [
    withMimir({
      ...createMimirMockAdapter(),
      lint: {
        ...createMimirMockAdapter().lint,
        getLintReport: async () => ({
          issues: [],
          pagesChecked: 42,
          summary: { error: 0, warn: 0, info: 0 },
        }),
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
        getLintReport: async () => {
          throw new Error('Lint service unavailable');
        },
      },
    }),
  ],
};
