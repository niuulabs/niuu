import type { Meta, StoryObj } from '@storybook/react';
import { Rune } from './Rune';

const meta: Meta<typeof Rune> = {
  title: 'Primitives/Rune',
  component: Rune,
  args: { glyph: 'ᚠ' },
};
export default meta;

type Story = StoryObj<typeof Rune>;

export const Default: Story = {};
export const Muted: Story = { args: { muted: true } };
export const Large: Story = { args: { size: 32 } };

export const PluginGlyphs: Story = {
  render: () => (
    <div style={{ display: 'flex', gap: 20, alignItems: 'center' }}>
      {['ᚠ', 'ᛗ', 'ᚱ', 'ᚦ', 'ᚢ', 'ᛚ'].map((g) => (
        <Rune key={g} glyph={g} size={28} />
      ))}
    </div>
  ),
};
