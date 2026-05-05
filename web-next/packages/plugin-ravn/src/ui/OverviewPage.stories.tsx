import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { OverviewPage } from './OverviewPage';
import {
  createMockRavenStream,
  createMockTriggerStore,
  createMockSessionStream,
  createMockBudgetStream,
} from '../adapters/mock';

function makeServices(overrides?: Record<string, unknown>) {
  return {
    'ravn.ravens': createMockRavenStream(),
    'ravn.triggers': createMockTriggerStore(),
    'ravn.sessions': createMockSessionStream(),
    'ravn.budget': createMockBudgetStream(),
    ...overrides,
  };
}

function withProviders(services: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Decorator({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={services}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

const meta: Meta<typeof OverviewPage> = {
  title: 'Plugins / Ravn / OverviewPage',
  component: OverviewPage,
  parameters: { layout: 'fullscreen' },
  decorators: [
    (Story) => {
      const D = withProviders(makeServices());
      return (
        <div style={{ height: '100vh', background: 'var(--color-bg-primary)' }}>
          <D>
            <Story />
          </D>
        </div>
      );
    },
  ],
};

export default meta;

type Story = StoryObj<typeof OverviewPage>;

/** Live mock data — overview with 6 ravens, 5 triggers, budget spend visible. */
export const Default: Story = {};

/** Fleet is empty — all lists show empty states. */
export const Empty: Story = {
  decorators: [
    (Story) => {
      const D = withProviders(
        makeServices({
          'ravn.ravens': {
            listRavens: () => Promise.resolve([]),
            getRaven: () => Promise.resolve(null),
          },
          'ravn.triggers': { listTriggers: () => Promise.resolve([]) },
          'ravn.sessions': {
            listSessions: () => Promise.resolve([]),
            getSession: () => Promise.resolve(null),
            getMessages: () => Promise.resolve([]),
          },
        }),
      );
      return (
        <div style={{ height: '100vh', background: 'var(--color-bg-primary)' }}>
          <D>
            <Story />
          </D>
        </div>
      );
    },
  ],
};
