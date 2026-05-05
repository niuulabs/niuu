import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { IngestPage } from './IngestPage';
import { createMimirMockAdapter } from '../adapters/mock';

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function withMimir() {
  const svc = createMimirMockAdapter();
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

const meta: Meta<typeof IngestPage> = {
  title: 'Mímir/IngestPage',
  component: IngestPage,
  decorators: [withMimir()],
  parameters: { layout: 'fullscreen' },
};
export default meta;

type Story = StoryObj<typeof IngestPage>;

export const Default: Story = {};
