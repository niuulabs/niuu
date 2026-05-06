import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { TemplatesPage } from './TemplatesPage';
import { createMockTemplateStore } from '../adapters/mock';
import type { ITemplateStore } from '../ports/ITemplateStore';

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function withVolundr(store?: ITemplateStore) {
  const svc = store ?? createMockTemplateStore();
  const Decorator = (Story: React.ComponentType) => (
    <QueryClientProvider client={makeClient()}>
      <ServicesProvider services={{ 'volundr.templates': svc }}>
        <Story />
      </ServicesProvider>
    </QueryClientProvider>
  );
  return Decorator;
}

const meta: Meta<typeof TemplatesPage> = {
  title: 'Völundr/TemplatesPage',
  component: TemplatesPage,
  decorators: [withVolundr()],
  parameters: { layout: 'fullscreen' },
};
export default meta;

type Story = StoryObj<typeof TemplatesPage>;

export const Default: Story = {};

export const Empty: Story = {
  decorators: [
    withVolundr({
      ...createMockTemplateStore(),
      listTemplates: async () => [],
    }),
  ],
};

export const LoadError: Story = {
  decorators: [
    withVolundr({
      ...createMockTemplateStore(),
      listTemplates: async () => {
        throw new Error('Template service unavailable');
      },
    }),
  ],
};
