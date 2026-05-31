import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { RavnsPage } from './RavnsPage';
import { createMimirMockAdapter } from '../adapters/mock';
import { toRavnWardenSummary, type IRavnWardenService } from '../application/useRavns';
import type { IMimirService } from '../ports';

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function withMimir(service?: IMimirService) {
  const svc = service ?? createMimirMockAdapter();
  async function listWardens() {
    const bindings = await svc.mounts.listRavnBindings();
    return bindings.map(toRavnWardenSummary);
  }
  const ravnWardens: IRavnWardenService = {
    listWardens,
    async getWarden(id) {
      const wardens = await listWardens();
      return wardens.find((warden) => warden.id === id) ?? wardens[0]!;
    },
    async createWarden(req) {
      return {
        ...toRavnWardenSummary({
          ravnId: req.name.toLowerCase().replace(/[^a-z0-9]+/g, '-'),
          ravnRune: 'ᚱ',
          role: 'index',
          state: 'offline',
          mountNames: req.mountNames ?? [],
          writeMount: req.writeMount ?? '',
          lastDream: null,
          bio: `${req.name} warden`,
          pagesTouched: 0,
          expertise: req.categoryScope ?? [],
          tools: ['ravn', 'mimir'],
        }),
        name: req.name,
      };
    },
    subscribeWarden() {
      return () => {};
    },
    async observeWarden(id) {
      const wardens = await listWardens();
      return wardens.find((warden) => warden.id === id) ?? wardens[0]!;
    },
    async installWarden(id) {
      const wardens = await listWardens();
      return wardens.find((warden) => warden.id === id) ?? wardens[0]!;
    },
    async startWarden(id) {
      const wardens = await listWardens();
      return wardens.find((warden) => warden.id === id) ?? wardens[0]!;
    },
    async stopWarden(id) {
      const wardens = await listWardens();
      return wardens.find((warden) => warden.id === id) ?? wardens[0]!;
    },
    async uninstallWarden(id) {
      const wardens = await listWardens();
      return wardens.find((warden) => warden.id === id) ?? wardens[0]!;
    },
  };
  function MimirDecorator(Story: React.ComponentType) {
    return (
      <QueryClientProvider client={makeClient()}>
        <ServicesProvider services={{ mimir: svc, 'ravn.wardens': ravnWardens }}>
          <Story />
        </ServicesProvider>
      </QueryClientProvider>
    );
  }
  return MimirDecorator;
}

const meta: Meta<typeof RavnsPage> = {
  title: 'Mímir/RavnsPage',
  component: RavnsPage,
  decorators: [withMimir()],
  parameters: { layout: 'fullscreen' },
};
export default meta;

type Story = StoryObj<typeof RavnsPage>;

export const Default: Story = {};

export const Empty: Story = {
  decorators: [
    withMimir({
      ...createMimirMockAdapter(),
      mounts: {
        ...createMimirMockAdapter().mounts,
        listRavnBindings: async () => [],
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
        listRavnBindings: async () => {
          throw new Error('Ravn binding service unavailable');
        },
      },
    }),
  ],
};
