import { afterEach, describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ToolGroupBlock } from './ToolGroupBlock';
import type { ToolResultBlock, ToolUseBlock } from './groupContentBlocks';

const toolUse = (name: string, input: Record<string, unknown>, id: string): ToolUseBlock => ({
  type: 'tool_use',
  id,
  name,
  input,
});
const done = (id: string, content = 'ok', is_error = false): ToolResultBlock => ({
  type: 'tool_result',
  tool_use_id: id,
  content,
  ...(is_error ? { is_error } : {}),
});

const burst = [
  { block: toolUse('Bash', { command: 'pytest tests/' }, 'b1'), result: done('b1') },
  {
    block: toolUse(
      'Edit',
      { file_path: 'src/gates.py', old_string: 'a', new_string: 'b\nc' },
      'e1',
    ),
    result: done('e1'),
  },
  { block: toolUse('Read', { file_path: 'a.ts' }, 'r1'), result: done('r1') },
  { block: toolUse('Read', { file_path: 'b.ts' }, 'r2'), result: done('r2') },
];

afterEach(() => {
  localStorage.clear();
});

describe('ToolGroupBlock — one row per burst', () => {
  it('shows family chips, a humanised summary, the diffstat and a done glyph', () => {
    render(<ToolGroupBlock toolName="Bash" blocks={burst} />);
    const row = screen.getByTestId('tool-group-block');
    expect(row).toHaveAttribute('data-status', 'done');
    expect(row.querySelectorAll('.niuu-chat-toolrun-chip')).toHaveLength(3);
    expect(row).toHaveTextContent('1 command · 1 file edited · 2 files read');
    expect(screen.getByTestId('tool-run-diffstat')).toHaveTextContent('+2 −1');
    expect(screen.getByRole('img', { name: 'Done' })).toBeInTheDocument();
    expect(screen.queryByTestId('tool-run-ticker')).toBeNull();
    expect(screen.queryAllByTestId('tool-block')).toHaveLength(0);
  });

  it('counts families beyond the third as +N', () => {
    const many = [
      ...burst,
      { block: toolUse('Grep', { pattern: 'p' }, 'g1'), result: done('g1') },
      { block: toolUse('WebFetch', { url: 'u' }, 'w1'), result: done('w1') },
    ];
    render(<ToolGroupBlock toolName="Bash" blocks={many} />);
    expect(
      screen.getByTestId('tool-group-block').querySelectorAll('.niuu-chat-toolrun-chip'),
    ).toHaveLength(4);
    expect(document.querySelector('.niuu-chat-toolrun-chip--more')).toHaveTextContent('+2');
  });

  it('omits the diffstat when nothing was edited', () => {
    render(<ToolGroupBlock toolName="Read" blocks={burst.slice(2)} />);
    expect(screen.queryByTestId('tool-run-diffstat')).toBeNull();
  });

  it('spins and shows a ticker for the latest call without a result while streaming', () => {
    const live = [
      burst[0]!,
      { block: toolUse('Bash', { command: 'pytest tests/ -n auto' }, 'b2') },
      { block: toolUse('Edit', { file_path: 'src/gates.py' }, 'e2') },
    ];
    render(<ToolGroupBlock toolName="Bash" blocks={live} isStreaming />);
    expect(screen.getByTestId('tool-group-block')).toHaveAttribute('data-status', 'running');
    expect(screen.getByRole('img', { name: 'Running' })).toBeInTheDocument();
    expect(screen.getByTestId('tool-run-ticker')).toHaveTextContent('Editing src/gates.py');
  });

  it('does not claim a call is running once the message has finished', () => {
    render(
      <ToolGroupBlock
        toolName="Bash"
        blocks={[burst[0]!, { block: toolUse('Bash', { command: 'x' }, 'b2') }]}
      />,
    );
    expect(screen.getByTestId('tool-group-block')).toHaveAttribute('data-status', 'done');
    expect(screen.queryByTestId('tool-run-ticker')).toBeNull();
  });

  it('shows a warning glyph when any result is an error', () => {
    render(
      <ToolGroupBlock
        toolName="Bash"
        blocks={[
          burst[0]!,
          { block: toolUse('Bash', { command: 'false' }, 'b2'), result: done('b2', 'boom', true) },
        ]}
      />,
    );
    expect(screen.getByTestId('tool-group-block')).toHaveAttribute('data-status', 'failed');
    expect(screen.getByRole('img', { name: 'A call failed' })).toBeInTheDocument();
  });

  it('renders a single call as a one-call row with its verb and subject', () => {
    const { rerender } = render(<ToolGroupBlock toolName="Bash" blocks={[burst[0]!]} />);
    const row = screen.getByTestId('tool-group-block');
    expect(row).toHaveTextContent('Ran pytest tests/');
    rerender(
      <ToolGroupBlock
        toolName="Bash"
        blocks={[{ block: toolUse('Bash', { command: 'pytest tests/' }, 'b9') }]}
        isStreaming
      />,
    );
    expect(screen.getByTestId('tool-group-block')).toHaveTextContent('Running pytest tests/');
    expect(screen.queryByTestId('tool-run-ticker')).toBeNull();
  });

  it('opens a right-hand sheet listing every call, the last one open, and Escape closes it', async () => {
    const user = userEvent.setup();
    render(<ToolGroupBlock toolName="Bash" blocks={burst} />);
    expect(screen.queryByRole('dialog')).toBeNull();
    await user.click(screen.getByRole('button', { name: /Open 4 tool calls/ }));
    const dialog = screen.getByRole('dialog', { name: '4 tool calls' });
    const blocks = within(dialog).getAllByTestId('tool-block');
    expect(blocks).toHaveLength(4);
    const expanded = blocks.map((block) =>
      block.querySelector('.niuu-chat-tool-header')?.getAttribute('aria-expanded'),
    );
    expect(expanded).toEqual(['false', 'false', 'false', 'true']);
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});

describe('ToolGroupBlock — every call', () => {
  it('renders the calls inline as ToolBlocks and pages long bursts', () => {
    localStorage.setItem('niuu.compactUx.toolRuns', 'cards');
    const blocks = Array.from({ length: 30 }, (_, i) => ({
      block: toolUse('Bash', { command: `command-${i}` }, `call-${i}`),
      result: done(`call-${i}`, `result-${i}`),
    }));
    render(<ToolGroupBlock toolName="Bash" blocks={blocks} />);
    expect(screen.queryByRole('button', { name: /Open 30 tool calls/ })).toBeNull();
    expect(screen.getAllByTestId('tool-block')).toHaveLength(25);
    expect(screen.queryByText('result-0')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Show 5 more calls/ }));
    expect(screen.getAllByTestId('tool-block')).toHaveLength(30);
  });
});
