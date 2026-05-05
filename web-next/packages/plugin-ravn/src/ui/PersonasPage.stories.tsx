import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { PersonasPage } from './PersonasPage';
import { createMockPersonaStore } from '../adapters/mock';

function makeWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const store = createMockPersonaStore();
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={{ 'ravn.personas': store }}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

const meta: Meta<typeof PersonasPage> = {
  title: 'Plugins/Ravn/PersonasPage',
  component: PersonasPage,
  parameters: {
    layout: 'fullscreen',
    a11y: {},
  },
  decorators: [
    (Story) => {
      const Wrapper = makeWrapper();
      return (
        <Wrapper>
          <div style={{ height: '100vh', background: 'var(--color-bg-primary)' }}>
            <Story />
          </div>
        </Wrapper>
      );
    },
  ],
};

export default meta;
type Story = StoryObj<typeof PersonasPage>;

export const Default: Story = {};
