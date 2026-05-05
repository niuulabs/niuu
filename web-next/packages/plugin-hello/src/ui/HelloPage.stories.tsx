import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import type { ReactNode } from 'react';
import type { Greeting, IHelloService } from '../ports';
import { HelloPage } from './HelloPage';

/**
 * These stories wrap HelloPage with a scripted mock IHelloService so each
 * variant (data / loading / error) is reachable without network. This mirrors
 * the pattern plugin consumers will use in their own tests + docs.
 */

const seed: Greeting[] = [
  { id: '1', text: 'hello from Storybook', mood: 'warm' },
  { id: '2', text: 'mock adapter wired via ServicesProvider', mood: 'curious' },
  { id: '3', text: 'ice-themed and composable', mood: 'cold' },
];

function buildWrapper(service: IHelloService) {
  return function Wrapper({ children }: { children: ReactNode }) {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity, gcTime: 0 } },
    });
    return (
      <QueryClientProvider client={qc}>
        <ServicesProvider services={{ hello: service }}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

const meta: Meta<typeof HelloPage> = {
  title: 'Plugins / Hello / HelloPage',
  component: HelloPage,
};
export default meta;

type Story = StoryObj<typeof HelloPage>;

export const Data: Story = {
  decorators: [
    (Story) => {
      const Wrapper = buildWrapper({
        async listGreetings() {
          return seed;
        },
      });
      return (
        <Wrapper>
          <Story />
        </Wrapper>
      );
    },
  ],
};

export const Loading: Story = {
  decorators: [
    (Story) => {
      const Wrapper = buildWrapper({
        listGreetings() {
          return new Promise(() => {
            /* never resolves — stays in loading state */
          });
        },
      });
      return (
        <Wrapper>
          <Story />
        </Wrapper>
      );
    },
  ],
};

export const Error: Story = {
  decorators: [
    (Story) => {
      const Wrapper = buildWrapper({
        async listGreetings() {
          throw new Error('upstream hello service unreachable');
        },
      });
      return (
        <Wrapper>
          <Story />
        </Wrapper>
      );
    },
  ],
};
