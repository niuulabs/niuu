import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { OverviewView } from './OverviewView';
import { createMimirMockAdapter } from '../adapters/mock';
import { toRavnWardenSummary, type IRavnWardenService } from '../application/useRavns';

function withProviders(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const mimir = createMimirMockAdapter();
  async function listWardens() {
    const bindings = await mimir.mounts.listRavnBindings();
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
  return (
    <QueryClientProvider client={client}>
      <ServicesProvider services={{ mimir, 'ravn.wardens': ravnWardens }}>{ui}</ServicesProvider>
    </QueryClientProvider>
  );
}

const meta = {
  title: 'Mimir/OverviewView',
  component: OverviewView,
  decorators: [(Story) => withProviders(<Story />)],
} satisfies Meta<typeof OverviewView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};
