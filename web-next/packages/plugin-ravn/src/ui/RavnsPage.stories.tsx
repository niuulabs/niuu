import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { RavensPage } from './RavensPage';
import {
  createMockRavenStream,
  createMockBudgetStream,
  createMockTriggerStore,
  createMockSessionStream,
} from '../adapters/mock';

function makeServices(overrides?: Record<string, unknown>) {
  return {
    'ravn.ravens': createMockRavenStream(),
    'ravn.budget': createMockBudgetStream(),
    'ravn.triggers': createMockTriggerStore(),
    'ravn.sessions': createMockSessionStream(),
    ...overrides,
  };
}

function makeDecorator(services: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Decorator({ children }: { children: React.ReactNode }) {
    return (
      <div
        style={{
          height: '100vh',
          background: 'var(--color-bg-primary)',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <QueryClientProvider client={client}>
          <ServicesProvider services={services}>{children}</ServicesProvider>
        </QueryClientProvider>
      </div>
    );
  };
}

const meta: Meta<typeof RavensPage> = {
  title: 'Plugins / Ravn / RavensPage',
  component: RavensPage,
  parameters: { layout: 'fullscreen' },
};

export default meta;

type Story = StoryObj<typeof RavensPage>;

/** Split layout — list on left, detail pane empty until a ravn is clicked. */
export const SplitLayout: Story = {
  decorators: [
    (Story) => {
      const D = makeDecorator(makeServices());
      localStorage.setItem('ravn.ravens.layout', '"split"');
      return (
        <D>
          <Story />
        </D>
      );
    },
  ],
};

/** Table layout — sortable flat table. */
export const TableLayout: Story = {
  decorators: [
    (Story) => {
      const D = makeDecorator(makeServices());
      localStorage.setItem('ravn.ravens.layout', '"table"');
      return (
        <D>
          <Story />
        </D>
      );
    },
  ],
};

/** Cards layout — grid of ravn cards. */
export const CardsLayout: Story = {
  decorators: [
    (Story) => {
      const D = makeDecorator(makeServices());
      localStorage.setItem('ravn.ravens.layout', '"cards"');
      return (
        <D>
          <Story />
        </D>
      );
    },
  ],
};

/** Split layout grouped by state. */
export const GroupedByState: Story = {
  decorators: [
    (Story) => {
      const D = makeDecorator(makeServices());
      localStorage.setItem('ravn.ravens.layout', '"split"');
      localStorage.setItem('ravn.ravens.group', '"state"');
      return (
        <D>
          <Story />
        </D>
      );
    },
  ],
};

/** Split layout grouped by location (model-derived). */
export const GroupedByLocation: Story = {
  decorators: [
    (Story) => {
      const D = makeDecorator(makeServices());
      localStorage.setItem('ravn.ravens.layout', '"split"');
      localStorage.setItem('ravn.ravens.group', '"location"');
      return (
        <D>
          <Story />
        </D>
      );
    },
  ],
};
