import type { Meta, StoryObj } from '@storybook/react';
import { PageTypeGlyph } from './PageTypeGlyph';

const meta = {
  title: 'Mimir/PageTypeGlyph',
  component: PageTypeGlyph,
  parameters: { layout: 'centered' },
} satisfies Meta<typeof PageTypeGlyph>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Entity: Story = { args: { type: 'entity', showLabel: true } };
export const Topic: Story = { args: { type: 'topic', showLabel: true } };
export const Directive: Story = { args: { type: 'directive', showLabel: true } };
export const Preference: Story = { args: { type: 'preference', showLabel: true } };
export const Decision: Story = { args: { type: 'decision', showLabel: true } };

export const AllTypes: Story = {
  render: () => (
    <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
      {(['entity', 'topic', 'directive', 'preference', 'decision'] as const).map((type) => (
        <PageTypeGlyph key={type} type={type} size={20} showLabel />
      ))}
    </div>
  ),
};
