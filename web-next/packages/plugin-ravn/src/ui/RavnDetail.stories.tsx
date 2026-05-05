import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { RavnDetail } from './RavnDetail';
import {
  createMockTriggerStore,
  createMockSessionStream,
  createMockBudgetStream,
} from '../adapters/mock';
import type { Ravn } from '../domain/ravn';

const ACTIVE_RAVN: Ravn = {
  id: 'a3f1b2c4-8e7d-4a6f-9b0c-1d2e3f4a5b6c',
  personaName: 'coding-agent',
  status: 'active',
  model: 'claude-sonnet-4-6',
  createdAt: '2026-04-15T09:12:34Z',
};

const SUSPENDED_RAVN: Ravn = {
  id: 'e1f2a3b4-5c6d-4e7f-8a9b-0c1d2e3f4a5b',
  personaName: 'investigator',
  status: 'suspended',
  model: 'claude-opus-4-6',
  createdAt: '2026-04-14T22:10:45Z',
};

const IDLE_RAVN: Ravn = {
  id: 'f5a6b7c8-9d0e-4f1a-2b3c-4d5e6f7a8b9c',
  personaName: 'health-auditor',
  status: 'idle',
  model: 'claude-sonnet-4-6',
  createdAt: '2026-04-14T18:33:07Z',
};

function makeDecorator(ravn: Ravn, allSectionsOpen = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (allSectionsOpen) {
    localStorage.removeItem('ravn.detail.sections.collapsed');
  }
  return function Decorator({ children }: { children: React.ReactNode }) {
    return (
      <div style={{ width: 360, height: '100vh', background: 'var(--color-bg-primary)' }}>
        <QueryClientProvider client={client}>
          <ServicesProvider
            services={{
              'ravn.triggers': createMockTriggerStore(),
              'ravn.sessions': createMockSessionStream(),
              'ravn.budget': createMockBudgetStream(),
            }}
          >
            {children}
          </ServicesProvider>
        </QueryClientProvider>
      </div>
    );
  };
  void ravn;
}

const meta: Meta<typeof RavnDetail> = {
  title: 'Plugins / Ravn / RavnDetail',
  component: RavnDetail,
  parameters: { layout: 'fullscreen' },
};

export default meta;

type Story = StoryObj<typeof RavnDetail>;

/** Active ravn — overview section open by default, state dot pulses. */
export const ActiveRavn: Story = {
  args: { ravn: ACTIVE_RAVN },
  decorators: [
    (Story) => {
      const D = makeDecorator(ACTIVE_RAVN);
      return (
        <D>
          <Story />
        </D>
      );
    },
  ],
};

/** Suspended ravn — suspend button disabled. */
export const SuspendedRavn: Story = {
  args: { ravn: SUSPENDED_RAVN },
  decorators: [
    (Story) => {
      const D = makeDecorator(SUSPENDED_RAVN);
      return (
        <D>
          <Story />
        </D>
      );
    },
  ],
};

/** Idle ravn — neutral state dot. */
export const IdleRavn: Story = {
  args: { ravn: IDLE_RAVN },
  decorators: [
    (Story) => {
      const D = makeDecorator(IDLE_RAVN);
      return (
        <D>
          <Story />
        </D>
      );
    },
  ],
};

/** All 6 sections expanded. */
export const AllSectionsExpanded: Story = {
  args: { ravn: ACTIVE_RAVN },
  decorators: [
    (Story) => {
      localStorage.setItem('ravn.detail.sections.collapsed', '[]');
      const D = makeDecorator(ACTIVE_RAVN, true);
      return (
        <D>
          <Story />
        </D>
      );
    },
  ],
};

/** With a close button. */
export const WithCloseButton: Story = {
  args: { ravn: ACTIVE_RAVN, onClose: () => console.log('close') },
  decorators: [
    (Story) => {
      const D = makeDecorator(ACTIVE_RAVN);
      return (
        <D>
          <Story />
        </D>
      );
    },
  ],
};
