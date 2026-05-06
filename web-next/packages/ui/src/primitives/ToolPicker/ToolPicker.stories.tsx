import type { Meta, StoryObj } from '@storybook/react';
import { useState } from 'react';
import { ToolPicker } from './ToolPicker';
import type { ToolRegistry } from '@niuulabs/domain';

const REGISTRY: ToolRegistry = [
  { id: 'read', group: 'fs', destructive: false, desc: 'Read a file from disk' },
  { id: 'write', group: 'fs', destructive: true, desc: 'Write or overwrite a file' },
  { id: 'list', group: 'fs', destructive: false, desc: 'List directory contents' },
  { id: 'bash', group: 'shell', destructive: true, desc: 'Run a shell command' },
  { id: 'git.status', group: 'git', destructive: false, desc: 'Show working tree status' },
  { id: 'git.diff', group: 'git', destructive: false, desc: 'Show diffs' },
  { id: 'git.push', group: 'git', destructive: true, desc: 'Push commits to remote' },
  { id: 'mimir.read', group: 'mimir', destructive: false, desc: 'Query Mímir knowledge store' },
  { id: 'mimir.write', group: 'mimir', destructive: false, desc: 'Write to Mímir knowledge store' },
  { id: 'mimir.delete', group: 'mimir', destructive: true, desc: 'Delete entries from Mímir' },
  { id: 'bus.emit', group: 'bus', destructive: false, desc: 'Emit an event to the event bus' },
];

const meta: Meta<typeof ToolPicker> = {
  title: 'Forms/ToolPicker',
  component: ToolPicker,
  parameters: { a11y: {} },
};

export default meta;
type Story = StoryObj<typeof ToolPicker>;

export const AllowList: Story = {
  render: function AllowListStory() {
    const [open, setOpen] = useState(false);
    const [selected, setSelected] = useState<string[]>(['read', 'mimir.read']);

    function toggle(id: string) {
      setSelected((prev) => (prev.includes(id) ? prev.filter((t) => t !== id) : [...prev, id]));
    }

    return (
      <>
        <button onClick={() => setOpen(true)} style={{ padding: '8px 16px', cursor: 'pointer' }}>
          Open Allow List ({selected.length} selected)
        </button>
        <ToolPicker
          open={open}
          onOpenChange={setOpen}
          registry={REGISTRY}
          selected={selected}
          onToggle={toggle}
          label="Allow list — select tools"
        />
      </>
    );
  },
};

export const DenyList: Story = {
  render: function DenyListStory() {
    const [open, setOpen] = useState(false);
    const [selected, setSelected] = useState<string[]>(['bash', 'git.push']);
    const allowed = ['read', 'mimir.read'];

    function toggle(id: string) {
      setSelected((prev) => (prev.includes(id) ? prev.filter((t) => t !== id) : [...prev, id]));
    }

    return (
      <>
        <button onClick={() => setOpen(true)} style={{ padding: '8px 16px', cursor: 'pointer' }}>
          Open Deny List ({selected.length} selected)
        </button>
        <ToolPicker
          open={open}
          onOpenChange={setOpen}
          registry={REGISTRY}
          selected={selected}
          excluded={allowed}
          onToggle={toggle}
          label="Deny list — select tools"
        />
      </>
    );
  },
};

export const EmptySelection: Story = {
  render: function EmptySelectionStory() {
    const [open, setOpen] = useState(false);
    const [selected, setSelected] = useState<string[]>([]);

    function toggle(id: string) {
      setSelected((prev) => (prev.includes(id) ? prev.filter((t) => t !== id) : [...prev, id]));
    }

    return (
      <>
        <button onClick={() => setOpen(true)} style={{ padding: '8px 16px', cursor: 'pointer' }}>
          Open (empty selection)
        </button>
        <ToolPicker
          open={open}
          onOpenChange={setOpen}
          registry={REGISTRY}
          selected={selected}
          onToggle={toggle}
        />
      </>
    );
  },
};

export const OpenByDefault: Story = {
  render: function OpenByDefaultStory() {
    const [open, setOpen] = useState(true);
    const [selected, setSelected] = useState<string[]>(['read', 'git.status']);

    function toggle(id: string) {
      setSelected((prev) => (prev.includes(id) ? prev.filter((t) => t !== id) : [...prev, id]));
    }

    return (
      <ToolPicker
        open={open}
        onOpenChange={setOpen}
        registry={REGISTRY}
        selected={selected}
        onToggle={toggle}
        label="Select tools"
      />
    );
  },
};
