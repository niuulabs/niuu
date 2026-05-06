import type { Meta, StoryObj } from '@storybook/react';
import { ToolBlock } from './ToolBlock';
import { ToolGroupBlock } from './ToolGroupBlock';

const meta: Meta<typeof ToolBlock> = {
  title: 'Chat/ToolBlock',
  component: ToolBlock,
};
export default meta;

type Story = StoryObj<typeof ToolBlock>;

const bashBlock = {
  type: 'tool_use' as const,
  id: '1',
  name: 'Bash',
  input: { command: 'npm run test', description: 'Run tests' },
};

const bashResult = {
  type: 'tool_result' as const,
  tool_use_id: '1',
  content: 'PASS src/foo.test.ts\n✓ all tests passed',
};

const editBlock = {
  type: 'tool_use' as const,
  id: '2',
  name: 'Edit',
  input: { file_path: '/src/app.ts', old_string: 'const x = 1', new_string: 'const x = 2' },
};

export const BashClosed: Story = { args: { block: bashBlock } };
export const BashOpen: Story = {
  args: { block: bashBlock, result: bashResult, defaultOpen: true },
};
export const EditOpen: Story = { args: { block: editBlock, defaultOpen: true } };

export const Group: StoryObj<typeof ToolGroupBlock> = {
  render: () => (
    <ToolGroupBlock
      toolName="Read"
      blocks={[
        { block: { type: 'tool_use', id: '1', name: 'Read', input: { file_path: '/a.ts' } } },
        { block: { type: 'tool_use', id: '2', name: 'Read', input: { file_path: '/b.ts' } } },
      ]}
    />
  ),
};
