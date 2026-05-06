import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ToolBlock } from './ToolBlock';
import { ToolGroupBlock } from './ToolGroupBlock';
import { groupContentBlocks, type ContentBlock } from './groupContentBlocks';
import { getToolLabel, getToolCategory } from './toolLabels';

const bashBlock = {
  type: 'tool_use' as const,
  id: '1',
  name: 'Bash',
  input: { command: 'ls -la', description: 'List files' },
};

describe('ToolBlock', () => {
  it('renders collapsed by default', () => {
    render(<ToolBlock block={bashBlock} />);
    expect(screen.getByRole('button')).toBeInTheDocument();
    // preview text IS shown while collapsed — detail pane is hidden
    expect(screen.queryByText('ls -la')).toBeInTheDocument();
  });

  it('expands on click to show detail', () => {
    render(<ToolBlock block={bashBlock} />);
    fireEvent.click(screen.getByRole('button'));
    expect(screen.getByText('ls -la')).toBeInTheDocument();
  });

  it('shows preview text when collapsed', () => {
    render(<ToolBlock block={bashBlock} />);
    expect(screen.getByText('ls -la')).toBeInTheDocument(); // preview
  });

  it('renders Edit tool with diff view when expanded', () => {
    const editBlock = {
      type: 'tool_use' as const,
      id: '2',
      name: 'Edit',
      input: { file_path: '/foo.ts', old_string: 'old', new_string: 'new' },
    };
    render(<ToolBlock block={editBlock} defaultOpen />);
    expect(screen.getByText('old')).toBeInTheDocument();
    expect(screen.getByText('new')).toBeInTheDocument();
  });

  it('applies category class to block', () => {
    const { container } = render(<ToolBlock block={bashBlock} />);
    expect(container.firstChild).toHaveClass('niuu-chat-tool-block--terminal');
  });

  it('renders Read tool with file path when expanded', () => {
    const readBlock = {
      type: 'tool_use' as const,
      id: '3',
      name: 'Read',
      input: { file_path: '/src/main.ts' },
    };
    render(<ToolBlock block={readBlock} defaultOpen />);
    expect(screen.getByText('/src/main.ts')).toBeInTheDocument();
  });

  it('renders Write tool with file path when expanded', () => {
    const writeBlock = {
      type: 'tool_use' as const,
      id: '4',
      name: 'Write',
      input: { file_path: '/out.txt', content: 'hello output' },
    };
    render(<ToolBlock block={writeBlock} defaultOpen />);
    expect(screen.getByText('/out.txt')).toBeInTheDocument();
  });

  it('renders generic tool with params when expanded', () => {
    const genericBlock = {
      type: 'tool_use' as const,
      id: '5',
      name: 'CustomTool',
      input: { param: 'value123' },
    };
    render(<ToolBlock block={genericBlock} defaultOpen />);
    expect(screen.getByText('param:')).toBeInTheDocument();
    expect(screen.getByText('value123')).toBeInTheDocument();
  });

  it('shows output when result provided (Bash)', () => {
    const toolResult = {
      type: 'tool_result' as const,
      tool_use_id: '1',
      content: 'file1.ts\nfile2.ts',
    };
    render(<ToolBlock block={bashBlock} result={toolResult} defaultOpen />);
    expect(screen.getByText(/file1\.ts/)).toBeInTheDocument();
  });

  it('shows truncation button when Bash output > 20 lines', () => {
    const longOutput = Array.from({ length: 25 }, (_, i) => `line${i + 1}`).join('\n');
    const toolResult = { type: 'tool_result' as const, tool_use_id: '1', content: longOutput };
    render(<ToolBlock block={bashBlock} result={toolResult} defaultOpen />);
    expect(screen.getByText('Show full output')).toBeInTheDocument();
  });

  it('shows full output after clicking show full', () => {
    const longOutput = Array.from({ length: 25 }, (_, i) => `line${i + 1}`).join('\n');
    const toolResult = { type: 'tool_result' as const, tool_use_id: '1', content: longOutput };
    render(<ToolBlock block={bashBlock} result={toolResult} defaultOpen />);
    fireEvent.click(screen.getByText('Show full output'));
    expect(screen.getByText(/line25/)).toBeInTheDocument();
  });

  it('shows Read output when result provided', () => {
    const readBlock = {
      type: 'tool_use' as const,
      id: '3',
      name: 'Read',
      input: { file_path: '/src/main.ts' },
    };
    const toolResult = { type: 'tool_result' as const, tool_use_id: '3', content: 'const x = 1;' };
    render(<ToolBlock block={readBlock} result={toolResult} defaultOpen />);
    expect(screen.getByText('const x = 1;')).toBeInTheDocument();
  });

  it('shows truncation for Read output > 20 lines', () => {
    const longOutput = Array.from({ length: 25 }, (_, i) => `line${i + 1}`).join('\n');
    const readBlock = {
      type: 'tool_use' as const,
      id: '6',
      name: 'Read',
      input: { file_path: '/big.ts' },
    };
    const toolResult = { type: 'tool_result' as const, tool_use_id: '6', content: longOutput };
    render(<ToolBlock block={readBlock} result={toolResult} defaultOpen />);
    expect(screen.getByText('Show full output')).toBeInTheDocument();
  });

  it('renders Glob preview', () => {
    const globBlock = {
      type: 'tool_use' as const,
      id: '7',
      name: 'Glob',
      input: { pattern: '**/*.ts' },
    };
    render(<ToolBlock block={globBlock} />);
    expect(screen.getByText('**/*.ts')).toBeInTheDocument();
  });

  it('renders Grep preview', () => {
    const grepBlock = {
      type: 'tool_use' as const,
      id: '8',
      name: 'Grep',
      input: { pattern: 'function foo' },
    };
    render(<ToolBlock block={grepBlock} />);
    expect(screen.getByText('function foo')).toBeInTheDocument();
  });

  it('renders WebSearch preview', () => {
    const wsBlock = {
      type: 'tool_use' as const,
      id: '9',
      name: 'WebSearch',
      input: { query: 'typescript generics' },
    };
    render(<ToolBlock block={wsBlock} />);
    expect(screen.getByText('typescript generics')).toBeInTheDocument();
  });

  it('renders WebFetch preview', () => {
    const wfBlock = {
      type: 'tool_use' as const,
      id: '10',
      name: 'WebFetch',
      input: { url: 'https://example.com' },
    };
    render(<ToolBlock block={wfBlock} />);
    expect(screen.getByText('https://example.com')).toBeInTheDocument();
  });

  it('renders Agent preview', () => {
    const agentBlock = {
      type: 'tool_use' as const,
      id: '11',
      name: 'Agent',
      input: { description: 'Run sub-agent task' },
    };
    render(<ToolBlock block={agentBlock} />);
    expect(screen.getByText('Run sub-agent task')).toBeInTheDocument();
  });

  it('renders generic tool output when result provided', () => {
    const genericBlock = {
      type: 'tool_use' as const,
      id: '12',
      name: 'CustomTool',
      input: { key: 'val' },
    };
    const toolResult = {
      type: 'tool_result' as const,
      tool_use_id: '12',
      content: 'custom output',
    };
    render(<ToolBlock block={genericBlock} result={toolResult} defaultOpen />);
    expect(screen.getByText(/custom output/)).toBeInTheDocument();
  });

  it('renders Write content preview when expanded', () => {
    const writeBlock = {
      type: 'tool_use' as const,
      id: '13',
      name: 'Write',
      input: { file_path: '/out.ts', content: 'export const x = 1;' },
    };
    render(<ToolBlock block={writeBlock} defaultOpen />);
    expect(screen.getByText('export const x = 1;')).toBeInTheDocument();
  });
});

describe('ToolGroupBlock', () => {
  const blocks = [
    { block: { type: 'tool_use' as const, id: '1', name: 'Read', input: { file_path: '/a.ts' } } },
    { block: { type: 'tool_use' as const, id: '2', name: 'Read', input: { file_path: '/b.ts' } } },
  ];

  it('renders collapsed with count badge', () => {
    render(<ToolGroupBlock toolName="Read" blocks={blocks} />);
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.queryByText('/a.ts')).not.toBeInTheDocument();
  });

  it('expands on click', () => {
    render(<ToolGroupBlock toolName="Read" blocks={blocks} />);
    fireEvent.click(screen.getByRole('button'));
    // Individual ToolBlocks render
    expect(screen.getAllByTestId('tool-block')).toHaveLength(2);
  });
});

describe('groupContentBlocks', () => {
  it('groups consecutive same-name tool_use blocks', () => {
    const blocks = [
      { type: 'tool_use', id: '1', name: 'Read', input: {} },
      { type: 'tool_use', id: '2', name: 'Read', input: {} },
    ];
    const result = groupContentBlocks(blocks as ContentBlock[]);
    expect(result).toHaveLength(1);
    expect(result[0].kind).toBe('group');
  });

  it('keeps single tool_use as single', () => {
    const blocks = [{ type: 'tool_use', id: '1', name: 'Bash', input: {} }];
    const result = groupContentBlocks(blocks as ContentBlock[]);
    expect(result[0].kind).toBe('single');
  });

  it('passes text blocks through', () => {
    const blocks = [{ type: 'text', text: 'hello' }];
    const result = groupContentBlocks(blocks as ContentBlock[]);
    expect(result[0]).toEqual({ kind: 'text', text: 'hello' });
  });

  it('skips unknown block types', () => {
    const blocks = [{ type: 'unknown_type' }];
    const result = groupContentBlocks(blocks as ContentBlock[]);
    expect(result).toHaveLength(0);
  });

  it('pairs tool_use with following tool_result', () => {
    const blocks = [
      { type: 'tool_use', id: '1', name: 'Read', input: {} },
      { type: 'tool_result', tool_use_id: '1', content: 'file content' },
      { type: 'tool_use', id: '2', name: 'Read', input: {} },
      { type: 'tool_result', tool_use_id: '2', content: 'more content' },
    ];
    const result = groupContentBlocks(blocks as ContentBlock[]);
    expect(result).toHaveLength(1); // both Read blocks grouped
    expect(result[0].kind).toBe('group');
    const group = result[0] as { kind: 'group'; blocks: Array<{ result?: { content?: string } }> };
    expect(group.blocks[0].result?.content).toBe('file content');
  });
});

describe('getToolLabel', () => {
  it('returns friendly label for known tools', () => {
    expect(getToolLabel('Bash')).toBe('Terminal');
    expect(getToolLabel('Read')).toBe('Read File');
  });

  it('formats MCP tool names with colon', () => {
    expect(getToolLabel('mcp__slack__send')).toBe('mcp:slack__send');
  });

  it('returns original name for unknown tools', () => {
    expect(getToolLabel('CustomTool')).toBe('CustomTool');
  });
});

describe('getToolCategory', () => {
  it('returns correct category', () => {
    expect(getToolCategory('Bash')).toBe('terminal');
    expect(getToolCategory('Read')).toBe('file');
    expect(getToolCategory('WebSearch')).toBe('web');
  });

  it('returns mcp for tools with __', () => {
    expect(getToolCategory('mcp__slack__send')).toBe('mcp');
  });

  it('returns default for unknown', () => {
    expect(getToolCategory('Unknown')).toBe('default');
  });
});
