import type { Meta, StoryObj } from '@storybook/react';
import { createMemoryHistory } from '@tanstack/react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  ConfigProvider,
  FeatureCatalogProvider,
  ServicesProvider,
  definePlugin,
} from '@niuulabs/plugin-sdk';
import { Shell } from './Shell';

// ---------------------------------------------------------------------------
// Mock plugins for stories
// ---------------------------------------------------------------------------

const mockPluginA = definePlugin({
  id: 'alpha',
  rune: 'ᚨ',
  title: 'Alpha',
  subtitle: 'first plugin',
  render: () => (
    <div style={{ padding: 'var(--space-6)', color: 'var(--color-text-primary)' }}>
      <h2 style={{ margin: '0 0 var(--space-2)' }}>Alpha</h2>
      <p style={{ color: 'var(--color-text-secondary)', margin: 0 }}>
        Content area for the Alpha plugin.
      </p>
    </div>
  ),
});

const mockPluginB = definePlugin({
  id: 'beta',
  rune: 'ᛒ',
  title: 'Beta',
  subtitle: 'second plugin',
  render: () => (
    <div style={{ padding: 'var(--space-6)', color: 'var(--color-text-primary)' }}>
      <h2 style={{ margin: '0 0 var(--space-2)' }}>Beta</h2>
      <p style={{ color: 'var(--color-text-secondary)', margin: 0 }}>
        Content area for the Beta plugin.
      </p>
    </div>
  ),
});

// ---------------------------------------------------------------------------
// Storybook wrapper — provides required context
// ---------------------------------------------------------------------------

const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

function ShellStoryWrapper({ children }: { children: React.ReactNode }) {
  return (
    <QueryClientProvider client={qc}>
      <ConfigProvider value={{ theme: 'ice', plugins: {}, services: {} }}>
        <ServicesProvider services={{}}>
          <FeatureCatalogProvider>{children}</FeatureCatalogProvider>
        </ServicesProvider>
      </ConfigProvider>
    </QueryClientProvider>
  );
}

// ---------------------------------------------------------------------------
// Meta
// ---------------------------------------------------------------------------

const meta: Meta<typeof Shell> = {
  title: 'Shell/Shell',
  component: Shell,
  decorators: [
    (Story) => (
      <ShellStoryWrapper>
        <Story />
      </ShellStoryWrapper>
    ),
  ],
  parameters: {
    layout: 'fullscreen',
  },
};

export default meta;
type Story = StoryObj<typeof Shell>;

// ---------------------------------------------------------------------------
// Stories
// ---------------------------------------------------------------------------

/** Two plugins loaded; starts on the Alpha plugin (first enabled). */
export const TwoPlugins: Story = {
  args: {
    plugins: [mockPluginA, mockPluginB],
    _testHistory: createMemoryHistory({ initialEntries: ['/alpha'] }),
  },
};

/** Single plugin loaded — rail has only one item. */
export const SinglePlugin: Story = {
  args: {
    plugins: [mockPluginA],
    _testHistory: createMemoryHistory({ initialEntries: ['/alpha'] }),
  },
};

/** No plugins — empty shell with no rail items. */
export const NoPlugins: Story = {
  args: {
    plugins: [],
    _testHistory: createMemoryHistory({ initialEntries: ['/'] }),
  },
};

/** Shell with a deep-linked route at /beta pre-selected. */
export const DeepLinkedBeta: Story = {
  args: {
    plugins: [mockPluginA, mockPluginB],
    _testHistory: createMemoryHistory({ initialEntries: ['/beta'] }),
  },
};
