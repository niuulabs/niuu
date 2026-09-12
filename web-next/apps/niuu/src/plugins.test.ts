import { describe, it, expect, vi } from 'vitest';
import { niuuConfigSchema } from '@niuulabs/plugin-sdk';
import { loadEnabledPlugins } from './plugins';
vi.mock('@niuulabs/plugin-volundr', () => ({
  volundrPlugin: { id: 'volundr', title: 'Forge', routes: () => [] },
}));
const disabled = Object.fromEntries(
  [
    'login',
    'setup',
    'volundr',
    'ting',
    'ravn',
    'mimir',
    'valkyrie',
    'observatory',
    'bifrost',
    'guild',
    'settings',
    'logout',
  ].map((id) => [id, { enabled: false }]),
);
describe('runtime plugin loading', () => {
  it('omits operator-disabled modules', async () => {
    expect(await loadEnabledPlugins(niuuConfigSchema.parse({ plugins: disabled }))).toEqual([]);
  });
  it('loads enabled plugins with their styles', async () => {
    const plugins = await loadEnabledPlugins(
      niuuConfigSchema.parse({ plugins: { ...disabled, volundr: { enabled: true } } }),
    );
    expect(plugins.map((plugin) => plugin.id)).toEqual(['volundr']);
  });
});
