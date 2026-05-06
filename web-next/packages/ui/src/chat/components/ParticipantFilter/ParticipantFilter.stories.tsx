import type { Meta, StoryObj } from '@storybook/react';
import { ParticipantFilter } from './ParticipantFilter';
import type { RoomParticipant } from '../../types';

const participants: ReadonlyMap<string, RoomParticipant> = new Map([
  ['peer-1', { peerId: 'peer-1', persona: 'Ada', color: '#38bdf8' }],
  ['peer-2', { peerId: 'peer-2', persona: 'Björk', color: '#a78bfa' }],
]);

const meta = {
  title: 'Chat/ParticipantFilter',
  component: ParticipantFilter,
  parameters: { layout: 'fullscreen' },
  args: {
    participants,
    activeFilter: 'all',
    onFilterChange: (f: string) => console.log('filter:', f),
    showInternal: false,
    onToggleInternal: () => console.log('toggle internal'),
  },
} satisfies Meta<typeof ParticipantFilter>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};
export const ShowingInternal: Story = { args: { showInternal: true } };
export const ActiveFilter: Story = { args: { activeFilter: 'peer-1' } };
