import { useState } from 'react';
import type { Meta, StoryObj } from '@storybook/react';
import { FilterBar, type FilterState } from './FilterBar';
import { FilterChip } from './FilterChip';
import { FilterToggle } from './FilterToggle';

const meta: Meta<typeof FilterBar> = {
  title: 'Data/FilterBar',
  component: FilterBar,
};
export default meta;

type Story = StoryObj<typeof FilterBar>;

function FilterBarDemo() {
  const [filters, setFilters] = useState<FilterState>({});
  const [activeOnly, setActiveOnly] = useState(false);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, padding: 16 }}>
      <FilterBar
        value={filters}
        onChange={setFilters}
        placeholder="Search sessions…"
        activeFilters={
          filters.status ? [{ key: 'status', label: 'Status', value: filters.status }] : undefined
        }
      >
        <FilterToggle label="Active only" active={activeOnly} onChange={setActiveOnly} />
      </FilterBar>
      <pre style={{ fontSize: 11 }}>{JSON.stringify({ filters, activeOnly }, null, 2)}</pre>
    </div>
  );
}

export const Interactive: Story = {
  render: () => <FilterBarDemo />,
};

export const WithPresetFilters: Story = {
  render: () => (
    <FilterBar
      value={{ q: 'dispatch', status: 'running', env: 'prod' }}
      onChange={() => {}}
      activeFilters={[
        { key: 'status', label: 'Status', value: 'running' },
        { key: 'env', label: 'Env', value: 'prod' },
      ]}
    />
  ),
};

export const ChipAlone: StoryObj<typeof FilterChip> = {
  render: () => (
    <div style={{ display: 'flex', gap: 8 }}>
      <FilterChip label="status" value="active" onRemove={() => {}} />
      <FilterChip label="env" value="prod" onRemove={() => {}} />
    </div>
  ),
};

export const ToggleStates: StoryObj<typeof FilterToggle> = {
  render: () => (
    <div style={{ display: 'flex', gap: 8 }}>
      <FilterToggle label="Active only" active={false} onChange={() => {}} />
      <FilterToggle label="Active only" active={true} onChange={() => {}} />
    </div>
  ),
};
